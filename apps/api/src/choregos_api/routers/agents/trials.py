# SPDX-License-Identifier: Apache-2.0
"""Trials of a draft agent (S32-03)."""

from __future__ import annotations

from fastapi import status

from ...audit import record
from ...deps import Db, Me
from ...errors import forbidden
from ...rbac import Permission
from ...schemas import (
    AgentSpec,
    AgentTrial,
    AgentTrialCreate,
)
from ...services.agent_access import (
    agent_in_org,
    enforce_decision,
    load_visible_agent,
    project_in_org,
    require_agent_reader,
)
from ...services.agent_evals import evaluate, suite_for
from ...services.agent_governance import (
    Action,
    decide,
)
from ...services.agent_releases import (
    production_spec,
)
from ...services.agents import (
    version_errors,
)
from ...services.hosted_images import pin_hosted_runtime
from ...services.memory_access import check_bindings_at_publish
from ...services.network import check_agent_network
from ...services.organisations import organisation_by_slug
from ...services.routing import new_trial_work_item
from ._common import _model_allowed
from ._router import router

#: The most a trial may spend, whatever is asked (S32-03).
TRIAL_CAP_USD = 0.50
TRIAL_DEFAULT_USD = 0.25


@router.post(
    "/orgs/{org}/agent-trials",
    response_model=AgentTrial,
    status_code=status.HTTP_201_CREATED,
    operation_id="startAgentTrial",
)
async def start_agent_trial(org: str, body: AgentTrialCreate, session: Db, principal: Me) -> AgentTrial:
    """Run a draft version once on a sample input, without publishing it (S32-03).

    The run machinery is the usual one — the orchestrator's `AgentTrial` prepares, starts and awaits
    one stage — on a trial work item that no workflow, tracker or board sees. The budget is capped at
    0.50 $ and at the draft's budget per run; the orchestrator tightens it further to the project's
    budget for the role (policy). The gateway key is minted at that amount: the trial cannot spend more.
    """
    from ...temporal import get_temporal, trial_run_id, trial_workflow_id

    require_agent_reader(principal, org)
    if not principal.can(Permission.PROJECT_READ, org, body.project):
        raise forbidden(f"trying an agent in {org}/{body.project} requires being a member of it")
    organisation = await organisation_by_slug(session, org)
    subject = None
    if body.agent:
        _, _, subject = await load_visible_agent(session, principal, org, body.agent)
    enforce_decision(decide(principal, org, subject, Action.TRIAL, project_slug=body.project))
    version_errors(body.spec)
    await _model_allowed(session, organisation, body.spec)
    await check_bindings_at_publish(session, principal, organisation, body.spec)
    await check_agent_network(session, organisation.id, body.spec)
    body.spec = await pin_hosted_runtime(session, organisation.id, body.spec)
    project_row = await project_in_org(session, organisation, body.project)
    publiee = None
    if body.agent:
        agent = await agent_in_org(session, organisation, body.agent)
        production_rev = await production_spec(session, agent)
        publiee = AgentSpec.model_validate(production_rev) if production_rev is not None else None
    plafonds = [TRIAL_CAP_USD, body.budget_usd or TRIAL_DEFAULT_USD]
    if body.spec.budget.run_usd is not None:
        plafonds.append(body.spec.budget.run_usd)
    budget = round(min(plafonds), 4)
    item = await new_trial_work_item(
        session,
        project_row,
        title=body.input.title or "Trial",
        body=body.input.body,
        created_by=principal.email,
        fields=body.input.fields,
    )
    await get_temporal().start_trial(
        trial_workflow_id(item.id),
        {
            "project_id": project_row.id,
            "work_item_id": item.id,
            "spec": body.spec.model_dump(mode="json"),
            "budget_usd": budget,
        },
    )
    item.temporal_wf_id = trial_workflow_id(item.id)
    await record(
        session,
        principal,
        "agent.trial",
        org_id=organisation.id,
        target_type="work_item",
        target_id=item.id,
        project=body.project,
        budget_usd=budget,
    )
    return AgentTrial(
        run_id=trial_run_id(item.id),
        work_item_id=item.id,
        project=body.project,
        budget_usd=budget,
        status="queued",
        evals=evaluate(body.spec, suite_for(body.spec, publiee)),
    )
