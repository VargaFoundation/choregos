# SPDX-License-Identifier: Apache-2.0
"""The preparation of a job step (ADR 0056): a command, run by the runner, with no model.

What an agent step needs and a job does not is left out on purpose: no model is resolved, no gateway
key is minted (a job that cannot reach a model cannot spend on one), no playbook is rendered, no
memory is read. What it shares with an agent step is kept: the run row and its token, the declared
inputs and outputs, the repository, the permissions, the admission check and the `run.queued` event.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from choregos_api.db.models import Run
from choregos_api.services import persist_event
from choregos_contracts import (
    AgentRef,
    Budget,
    Callbacks,
    EventType,
    JobSpec,
    ModelRef,
    Permissions,
    PlaybookRef,
    ProjectRef,
    RepoRef,
    StageInput,
    TransitionRef,
    WorkItemLinks,
    WorkItemRef,
)
from choregos_core import utcnow
from choregos_core.redaction import REDACTED
from temporalio.exceptions import ApplicationError

from ..org_egress import merged_domains, org_runner_hosts
from .base import without_secrets
from .images import named_image
from .plan import StagePlan

#: What `agent.backend` and `run.backend` say for a job: there is no agent backend.
JOB_BACKEND = "job"
#: The model placeholder of a job's StageInput: nothing is called, the runner never reads it.
NO_MODEL = ModelRef(litellm_model="none", base_url="")


def job_image(settings: Any, name: str | None) -> str | None:
    """The image reference a job's image name maps to; `None` runs it in the runner image."""
    return named_image(settings, "job", name)


def job_ref(command: list[str], image: str | None) -> str:
    """What the run's `playbook_checksum` records for a job: its image and the digest of its argv."""
    digest = sha256("\0".join(command).encode()).hexdigest()[:12]
    return f"job:{image or 'runner'}@sha256:{digest}"[:80]


async def prepare_job(
    session: Any,
    bundle: Any,
    item: Any,
    plan: StagePlan,
    run_id: str,
    existing: Run | None,
    settings: Any,
    admit: Any,
) -> dict[str, Any]:
    """Builds and records the StageInput of a job step; replayable by `run_id` like any stage."""
    job = dict(plan.job or {})
    command = [str(part) for part in job.get("command") or []]
    if not command:
        raise ApplicationError("a job step without a command", type="job_invalid", non_retryable=True)
    image = job_image(settings, job.get("image"))
    engine = bundle.engine
    budget = engine.budget_for(plan.role, item.size)
    if plan.max_minutes:
        budget = Budget(usd=budget.usd, max_turns=budget.max_turns, max_minutes=plan.max_minutes)
    # Admission runs as for an agent (a plugin may refuse a job too); the budget is the role's,
    # recorded but never spent: a job holds no gateway key.
    await admit(bundle, item, run_id, plan, JOB_BACKEND, budget.usd)
    allowed_paths = list(item.allowed_paths or []) or ["**"]
    documents = item.documents or {}
    depot = bundle.config.repo
    stage_input = StageInput(
        run_id=run_id,
        attempt=plan.attempt,
        project=ProjectRef(
            slug=bundle.slug,
            org=bundle.org_slug,
            test_command=depot.test_command if depot else "",
            lint_command=depot.lint_command if depot else "",
            typecheck_command=depot.typecheck_command if depot else "",
            fact_commands=dict(bundle.config.dod.facts),
        ),
        work_item=WorkItemRef(
            key=item.tracker_key,
            title=item.title,
            body=item.body_snapshot or "",
            url=item.url,
            size=item.size,
            risk=item.risk,
            links=WorkItemLinks(
                spec_comment=documents.get("spec_comment"),
                plan_comment=documents.get("plan_comment"),
                pr=item.pr_url,
            ),
        ),
        transition=TransitionRef(
            id=plan.transition_id,
            role=plan.role,
            **{"from": plan.from_state},
            to=plan.to_state,
            outputs=plan.outputs or [],
            inputs=plan.inputs or [],
            output_types=plan.output_types or {},
        ),
        repo=RepoRef(
            url=depot.url,
            base_branch=depot.default_branch,
            work_branch=bundle.config.branch_for(item.tracker_key),
            clone_depth=depot.clone_depth,
        )
        if depot
        else None,
        agent=AgentRef(backend=JOB_BACKEND),
        model=NO_MODEL,
        gateway_key=None,
        budget=budget,
        allowed_paths=allowed_paths,
        playbook=PlaybookRef(ref=job_ref(command, image)),
        permissions=Permissions(
            write_paths=allowed_paths,
            deny_commands=engine.deny_commands(),
            allow_domains=merged_domains(
                engine.allow_domains(), await org_runner_hosts(session, bundle.project.org_id)
            ),
            dod_iterations=0,
            max_findings=engine.max_findings_per_run(),
            unknown_requests=bundle.policy.sandbox.unknown_requests,
        ),
        # The run token lives in the run's Secret only (#519): `start_run` mints it.
        callbacks=Callbacks(api_url=f"{settings.callback_url}/api/v1/internal", run_token=REDACTED),
        job=JobSpec(
            command=command,
            image=image,
            tools={str(c): [str(o) for o in ops] for c, ops in dict(job.get("tools") or {}).items()},
        ),
    )
    payload = stage_input.model_dump(mode="json", by_alias=True)
    run = existing or Run(id=run_id, work_item_id=item.id, project_id=bundle.project.id)
    run.transition_id = plan.transition_id
    run.stage_role = plan.role
    run.attempt = plan.attempt
    run.actor = plan.actor
    run.backend = JOB_BACKEND
    run.model = None
    run.status = "queued"
    run.stage_input = payload
    run.gateway_key_id = None
    run.playbook_checksum = stage_input.playbook.ref
    run.allowed_paths = allowed_paths
    run.started_at = utcnow()
    if existing is None:
        session.add(run)
    await persist_event(
        session,
        EventType.RUN_QUEUED,
        project_id=bundle.project.id,
        work_item_id=item.id,
        project_slug=bundle.slug,
        subject=run_id,
        run_id=run_id,
        stage=plan.role,
        backend=JOB_BACKEND,
        model=None,
    )
    return {
        "run_id": run_id,
        "stage_input": without_secrets(payload, keep_gateway_key=False),
        "reused": False,
    }
