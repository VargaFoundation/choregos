# SPDX-License-Identifier: Apache-2.0
"""A hosted session's pod (S41-11, ADR 0080 §10): the activities `AgentSessionPod` runs.

Idempotent (ADR 0008). `start_session_pod` writes the session's stage input and asks the executor
for the pod: the runner in `serve-session` mode beside the agent's container, the same loopback
governance as a workflow step. A second start finds the pod running and starts nothing. The pod's
Secret carries the session's gateway key and a token whose subject is `session-{id}`; neither is in
the stage input, nor in a Temporal payload. `stop_session_pod` stops the pod, fails the turns still
waiting, and closes the session, which books its spend and revokes its key once.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from typing import Any

from choregos_adapters.errors import ConfigurationError
from choregos_api.db.models import AgentSessionRecord
from choregos_api.db.session import limiter_aux_organisations
from choregos_api.security import mint_run_token
from choregos_contracts import (
    AgentRef,
    Budget,
    Callbacks,
    ModelRef,
    PlaybookRef,
    ProjectRef,
    StageInput,
    TransitionRef,
    WorkItemRef,
)
from choregos_core import ExecRef, StageJobSpec, telemetry, utcnow
from choregos_core.hosted import agent_container
from choregos_core.redaction import REDACTED
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..config import get_settings
from ..org_egress import org_runner_hosts
from .base import db, project_bundle
from .execution import _runner_namespace, classe_d_execution, env_du_runner
from .run_network import network_permissions, run_network

#: The pod could not start on what the deployment or the version says: retrying changes nothing.
POD_REFUSED = "session_pod_refused"
#: The tools a session's runner serves the container itself, from the internal API.
SESSION_MCP = "choregos"
#: A response's model calls when the agent names no `limits.max_turns` (as a prompt session's).
DEFAULT_MAX_TURNS = 20


def _refused(message: str, kind: str = POD_REFUSED) -> ApplicationError:
    return ApplicationError(message, type=kind, non_retryable=True)


def _minutes_left(expires_at: datetime) -> int:
    aware = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=utcnow().tzinfo)
    return max(1, int((aware - utcnow()).total_seconds() // 60) + 1)


@activity.defn(name="start_session_pod")
async def start_session_pod(payload: dict[str, Any]) -> dict[str, Any]:
    """The session's pod, started once: `{state, kind, name, namespace}` as the record keeps it."""
    from choregos_api.secret_store import get_secret_store
    from choregos_api.services.agent_hosted_sessions import session_run_id
    from choregos_api.services.agent_sessions import secret_name
    from choregos_api.services.agents import AgentIndisponible, resoudre_l_agent
    from choregos_api.services.hosted_builds import builds_for

    from .stage import _hosted_call

    session_id = str(payload["session_id"])
    settings = get_settings()
    async with db() as session:
        record = await session.get(AgentSessionRecord, session_id)
        if record is None:
            raise _refused(f"no agent session {session_id}")
        if record.status != "open":
            return {"state": "stopped", "reason": record.reason}
        if record.pod is not None and record.pod.get("state") in ("running", "stopped"):
            return dict(record.pod)
        bundle = await project_bundle(session, record.project_id)
        served = f"{record.agent_slug}@{record.agent_version}"
        try:
            agent = await resoudre_l_agent(session, record.org_id, record.project_id, served)
        except AgentIndisponible as exc:
            raise _refused(str(exc)) from exc
        builds = await builds_for(session, record.org_id, [agent.spec])
        # Refused here, as for a step, when no pod could run the version as published.
        hosted = _hosted_call(agent, builds, session=True)
        if hosted is None:
            raise _refused(f"the agent {served} is not a hosted agent")
        # A session works on no repository: whatever the version asks, its container gets no tree.
        hosted = hosted.model_copy(update={"workspace": "none"})
        run_id = session_run_id(session_id)
        minutes = _minutes_left(record.expires_at)
        org_hosts = await org_runner_hosts(session, record.org_id)
        stage_input = StageInput(
            run_id=run_id,
            # The stage span's context, derived from the run id (ADR 0018): the runner's span hangs off it.
            trace_parent=telemetry.traceparent(run_id),
            attempt=1,
            project=ProjectRef(slug=bundle.slug, org=bundle.org_slug),
            work_item=WorkItemRef(key=f"session:{session_id}", title=f"Session with {served}"),
            transition=TransitionRef(id="session", role="session", **{"from": "open"}, to="open"),
            agent=AgentRef(backend="hosted"),
            model=ModelRef(litellm_model=record.model, base_url=settings.gateway_url),
            hosted=hosted,
            # In the pod's Secret, never here.
            gateway_key=None,
            # The session's key carries its money cap; `usd` here is what the runner reports against.
            budget=Budget(
                usd=agent.spec.budget.run_usd or 1.0,
                max_turns=agent.spec.limits.max_turns or DEFAULT_MAX_TURNS,
                max_minutes=minutes,
            ),
            playbook=PlaybookRef(ref=f"session:{served}"),
            permissions=network_permissions(
                run_network(bundle, agent.spec.network, settings, org_hosts),
                unknown_requests=bundle.policy.sandbox.unknown_requests,
            ),
            callbacks=Callbacks(api_url=f"{settings.callback_url}/api/v1/internal", run_token=REDACTED),
        )
        record.stage_input = stage_input.model_dump(mode="json", by_alias=True)
        keys = await get_secret_store().get_many(session, record.org_id, [secret_name(session_id)])
        namespace = _runner_namespace(settings, bundle.slug)
        spec = StageJobSpec(
            run_id=run_id,
            project_slug=bundle.slug,
            namespace=namespace,
            runner_image=settings.runner_image,
            api_url=stage_input.callbacks.api_url,
            run_token=mint_run_token(
                run_id,
                project_slug=bundle.slug,
                work_item_key=stage_input.work_item.key,
                ttl_minutes=minutes + 15,
            ),
            gateway_key=keys.get(secret_name(session_id)) or None,
            stage_input=stage_input,
            timeout_minutes=minutes + 10,
            runtime_class=classe_d_execution(settings, bundle.policy),
            env=env_du_runner(settings, namespace),
            labels={
                "choregos/project": bundle.slug,
                "choregos/run-id": run_id,
                "choregos/agent-session": session_id,
            },
            # The runner serves the session's tools on its loopback MCP listener itself.
            agent_container=agent_container(stage_input, mcp_servers=[SESSION_MCP]),
            runner_command=["serve-session"],
        )
        await session.commit()  # the stage input is readable before the pod asks for it
        # The scope of the transaction ended with the commit (RLS is transaction-local).
        await limiter_aux_organisations(session, "*")
        try:
            ref = await bundle.adapters.executor.start(spec)
        except ConfigurationError as refusal:
            # A refusal that names itself (`hosted_unsupported_cluster`, S41-16) keeps its name.
            raise _refused(str(refusal), getattr(refusal, "code", POD_REFUSED)) from refusal
        from choregos_api.services.agent_sessions import journal

        record = await session.get(AgentSessionRecord, session_id)
        assert record is not None
        record.pod = {"state": "running", "kind": str(ref.kind), "name": ref.name, "namespace": ref.namespace}
        await journal(session, record, "pod.started", {"kind": str(ref.kind), "image": hosted.image})
        return dict(record.pod)


@activity.defn(name="stop_session_pod")
async def stop_session_pod(payload: dict[str, Any]) -> dict[str, Any]:
    """Stops the pod (gone already is not an error), fails the turns still waiting, closes the
    session with `reason`. Replayable: a stopped pod and a closed session stay as they are."""
    from choregos_api.services.agent_hosted_sessions import fail_open_turns, session_run_id
    from choregos_api.services.agent_sessions import close_session, journal

    session_id = str(payload["session_id"])
    reason = str(payload.get("reason") or "closed")
    async with db() as session:
        record = await session.get(AgentSessionRecord, session_id)
        if record is None:
            return {"state": "stopped", "failed_turns": 0}
        pod = dict(record.pod or {})
        if pod.get("name") and pod.get("state") != "stopped":
            bundle = await project_bundle(session, record.project_id)
            ref = ExecRef(
                kind=pod.get("kind") or "fake",
                name=str(pod["name"]),
                namespace=pod.get("namespace"),
                run_id=session_run_id(session_id),
            )
            with contextlib.suppress(Exception):  # gone already, or never started
                await bundle.adapters.executor.cancel(ref)
        stopped = pod.get("state") == "stopped"
        record.pod = {**pod, "state": "stopped"}
        failed = await fail_open_turns(session, record, reason)
        if not stopped:
            await journal(session, record, "pod.stopped", {"reason": reason})
        await close_session(session, record, reason=reason)
        return {"state": "stopped", "failed_turns": failed}


__all__ = ["POD_REFUSED", "start_session_pod", "stop_session_pod"]
