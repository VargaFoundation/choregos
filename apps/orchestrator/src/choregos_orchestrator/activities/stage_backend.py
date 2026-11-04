# SPDX-License-Identifier: Apache-2.0
"""Backend selection for a stage: which backend an agent runs on, and the refusals around it.

Helpers of `stage`, which re-exports them; none of them is a Temporal activity.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog
from choregos_api.db.models import BackendRegistryRow, Run
from choregos_contracts import (
    HostedCall,
    PromptCall,
    StageRole,
)
from choregos_core import (
    ModelResolutionError,
    ModelResolver,
    backend_enabled,
)
from choregos_core.cross_review import reviewer_candidates
from sqlalchemy import select

from .plan import StagePlan
from .remote import A2A_BACKEND

logger = structlog.get_logger("choregos.stage")


CODE_BACKEND = "code"


PROMPT_BACKEND = "prompt"


HOSTED_BACKEND = "hosted"


#: Runtimes no step can run (ADR 0080): a voice agent runs only as a session.
STEPLESS_RUNTIMES = frozenset({"voice"})


def _fixed_backend(agent: Any) -> bool:
    """Whether the agent's runtime fixes its backend (`code`, `prompt`, `hosted`): no rotation swaps it."""
    runtime = getattr(getattr(agent, "spec", None), "runtime", None)
    return runtime is not None and runtime.kind in {CODE_BACKEND, PROMPT_BACKEND, HOSTED_BACKEND}


def _prompt_call(agent: Any) -> PromptCall | None:
    """The step a prompt agent carries (ADR 0080), from its version's runtime; `None` for any other."""
    runtime = getattr(getattr(agent, "spec", None), "runtime", None)
    if runtime is None or runtime.kind != "prompt":
        return None
    return PromptCall(
        max_output_tokens=runtime.max_output_tokens,
        tool_choice=runtime.tool_choice,
        response_format=runtime.response_format,
    )


def _hosted_call(
    agent: Any, builds: Mapping[str, Any] | None = None, *, session: bool = False
) -> HostedCall | None:
    """The step a hosted agent carries (ADR 0080), from its version's runtime; `None` for any other.

    Refused, never retried, when no pod could run it as published: an image without a digest, a zip
    whose build is not `ready`, or, for a workflow step, a version that serves only `invocations_ws`
    (a `session`'s pod relays that socket, S41-21)."""
    from temporalio.exceptions import ApplicationError

    runtime = getattr(getattr(agent, "spec", None), "runtime", None)
    if runtime is None or runtime.kind != "hosted":
        return None
    who = f"`{agent.agent.slug}` v{agent.version}"
    socket = "invocations_ws" in runtime.protocols
    protocol = next((p for p in runtime.protocols if p in ("responses", "invocations")), None)
    if protocol is None and session and socket:
        protocol = "responses"  # never sent: a session reaches it only on `/realtime`
    if protocol is None:
        raise ApplicationError(
            f"the hosted agent {who} serves only `invocations_ws`; a workflow step is sent over "
            "`responses` or `invocations`",
            type="hosted_protocol_unsupported",
            non_retryable=True,
        )
    return HostedCall(
        protocol=protocol,
        image=_hosted_image(runtime, who, builds),
        port=runtime.port,
        readiness_timeout_s=runtime.readiness_timeout_s,
        termination_grace_s=runtime.termination_grace_s,
        workspace=runtime.workspace,
        env=dict(runtime.env),
        agent_slug=agent.agent.slug,
        agent_version=agent.version,
        socket=socket and session,
    )


def _hosted_image(runtime: Any, who: str, builds: Mapping[str, Any] | None = None) -> str:
    """`ref@sha256:…`: the digest publication pinned, or the image a zip's build produced, as its
    `hosted_builds` row says (the version's own `build` when there is no row)."""
    from temporalio.exceptions import ApplicationError

    source = runtime.source
    if source.type == "zip":
        build = runtime.build
        row = (builds or {}).get(build.key) if build is not None else None
        status = (row or build).status if build is not None else "not started"
        image = (row or build).image if build is not None else None
        if status != "ready" or not image or "@sha256:" not in image:
            raise ApplicationError(
                f"the hosted agent {who} is built from a zip whose build is {status}: a step runs "
                "only an image a `ready` build produced",
                type="hosted_build_not_ready",
                non_retryable=True,
            )
        return str(image)
    if not source.digest:
        raise ApplicationError(
            f"the hosted agent {who} names `{source.ref}` without a digest: publication pins every "
            "hosted image, and a step never runs a tag",
            type="hosted_image_unpinned",
            non_retryable=True,
        )
    return f"{source.ref.split('@', 1)[0]}@{source.digest}"


def _code_runtime(agent: Any) -> Any:
    """The `code` runtime of the resolved agent version, or `None` for any other."""
    runtime = getattr(getattr(agent, "spec", None), "runtime", None)
    return runtime if runtime is not None and runtime.kind == "code" else None


async def _backend_row(session: Any, backend: str) -> Any:
    return (
        await session.execute(select(BackendRegistryRow).where(BackendRegistryRow.name == backend))
    ).scalar_one_or_none()


def _require_backend_allowed(agent: Any, bundle: Any, backend: str) -> None:
    """Refuse the stage when the project's `agent.allowed_backends` does not list its backend.

    An empty list restricts nothing. A runtime that fixes its own backend is not concerned. The
    refusal has the same typed, non retryable shape as a backend the platform disabled."""
    from temporalio.exceptions import ApplicationError

    if _fixed_backend(agent):
        return
    allowed = list(bundle.config.agent.allowed_backends)
    if not allowed or backend in allowed:
        return
    logger.info("stage refused: backend not allowed", backend=backend, allowed=allowed)
    raise ApplicationError(
        f"the agent backend `{backend}` is not one this project allows ({', '.join(allowed)}): "
        "add it to the project's agent settings, or choose another backend.",
        type="BackendDisabled",
        non_retryable=True,
    )


async def _require_backend_enabled(session: Any, agent: Any, backend: str) -> None:
    """Refuse the stage when the platform administrator disabled its coding-agent backend.

    Read from the `agent_backends` table inside the activity (ADR 0008: a replay reads it again
    and a stage that already started is never re-checked). No row means nothing was ever
    switched off: the backend stays enabled (fresh install). The refusal is a typed, non
    retryable error the interpreter escalates as a configuration refusal."""
    from temporalio.exceptions import ApplicationError

    if _fixed_backend(agent):
        return
    row = await _backend_row(session, backend)
    if backend_enabled(backend, row.enabled if row else None):
        return
    if row is None:
        raise ApplicationError(
            f"the agent backend `{backend}` is disabled by the platform administrator (it was never "
            "enabled on the platform page). Enable it there, or choose another backend.",
            type="BackendDisabled",
            non_retryable=True,
        )
    reason = f" Reason given: {row.disabled_reason}." if row.disabled_reason else ""
    logger.info("stage refused: backend disabled", backend=backend)
    raise ApplicationError(
        f"the agent backend `{backend}` is disabled by the platform administrator.{reason} "
        "Enable it on the platform page, or choose another backend.",
        type="BackendDisabled",
        non_retryable=True,
    )


async def _reviewer_backend(
    session: Any,
    bundle: Any,
    item: Any,
    wanted: str,
    resolver: ModelResolver,
    plan: StagePlan,
) -> str:
    """A reviewer that is not the implementer (policy `review.cross_backend`).

    The guarantee is a mechanism: if the backend planned for the review is the one that wrote the
    code, another one is taken, among those the project allows and that accept the requested
    model. If none fits, the original is kept and that is said: blocking a work item because only
    one backend is installed would be worse than the degraded review, and
    `GET /projects/{id}/metrics/cross-backend` counts these cases.
    """
    implementer = (
        await session.execute(
            select(Run.backend)
            .where(
                Run.work_item_id == item.id,
                Run.stage_role == str(StageRole.IMPLEMENT),
                Run.backend.is_not(None),
            )
            .order_by(Run.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not implementer or implementer != wanted:
        return wanted
    for candidate in reviewer_candidates(implementer, list(bundle.config.agent.allowed_backends)):
        stored = await _backend_row(session, candidate)
        if not backend_enabled(candidate, stored.enabled if stored else None):
            continue  # S42-07c: a backend the platform disabled is not a reviewer
        try:
            resolver.resolve(
                plan.model_request, project=bundle.config, size=item.size, backend=candidate, role=plan.role
            )
        except ModelResolutionError:
            continue
        logger.info(
            "cross review",
            project=bundle.slug,
            work_item=item.tracker_key,
            implementer=implementer,
            reviewer=candidate,
        )
        return str(candidate)
    logger.warning(
        "cross review impossible",
        project=bundle.slug,
        work_item=item.tracker_key,
        implementer=implementer,
        reason="no other backend accepts the requested model",
    )
    return wanted


def _agent_plan(plan: StagePlan, spec: Any) -> StagePlan:
    """The plan, with what the agent's version sets: model, backend, and limits that only
    tighten the actor's."""
    from dataclasses import replace

    from temporalio.exceptions import ApplicationError

    def stricter(actor_limit: int | None, agent_limit: int | None) -> int | None:
        values = [v for v in (actor_limit, agent_limit) if v is not None]
        return min(values) if values else None

    runtime = getattr(spec, "runtime", None)
    backend = spec.backend
    if runtime is not None and runtime.kind == "code":
        # A code agent runs as the `code` backend and no other (ADR 0063): an actor that names
        # `codex` for it contradicts the version, which a retry would not change.
        if plan.backend and plan.backend != CODE_BACKEND:
            raise ApplicationError(
                f"the actor asks for the backend `{plan.backend}`, but the agent is written in code "
                f"(runtime `code`): drop `backend:` from the actor",
                type="AgentUnavailable",
                non_retryable=True,
            )
        backend = CODE_BACKEND
    elif runtime is not None and runtime.kind == "a2a":
        # A remote agent runs as no local backend (ADR 0062); an actor naming one contradicts it.
        if plan.backend and plan.backend != A2A_BACKEND:
            raise ApplicationError(
                f"the actor asks for the backend `{plan.backend}`, but the agent is remote (runtime "
                f"`a2a`): drop `backend:` from the actor",
                type="AgentUnavailable",
                non_retryable=True,
            )
        backend = A2A_BACKEND
    elif runtime is not None and runtime.kind == "prompt":
        # A prompt agent is the runner's own tool loop (ADR 0080): no coding agent to choose.
        if plan.backend and plan.backend != PROMPT_BACKEND:
            raise ApplicationError(
                f"the actor asks for the backend `{plan.backend}`, but the agent is a prompt agent "
                f"(runtime `prompt`): drop `backend:` from the actor",
                type="AgentUnavailable",
                non_retryable=True,
            )
        backend = PROMPT_BACKEND
    elif runtime is not None and runtime.kind == "hosted":
        # A hosted agent is its own container (ADR 0080): no coding agent to choose either.
        if plan.backend and plan.backend != HOSTED_BACKEND:
            raise ApplicationError(
                f"the actor asks for the backend `{plan.backend}`, but the agent is a hosted agent "
                f"(runtime `hosted`): drop `backend:` from the actor",
                type="AgentUnavailable",
                non_retryable=True,
            )
        backend = HOSTED_BACKEND
    elif runtime is not None and runtime.kind in STEPLESS_RUNTIMES:
        # ADR 0080: a voice agent never runs as a step; a step that started one would run nothing.
        raise ApplicationError(
            f"the agent's runtime `{runtime.kind}` cannot run as a workflow step "
            "(a voice agent runs only as a session)",
            type="AgentUnavailable",
            non_retryable=True,
        )
    elif runtime is not None and runtime.backend:
        backend = runtime.backend

    return replace(
        plan,
        model_request=spec.model or plan.model_request,
        backend=plan.backend or backend,
        max_turns=stricter(plan.max_turns, spec.limits.max_turns),
        max_minutes=stricter(plan.max_minutes, spec.limits.max_minutes),
    )
