# SPDX-License-Identifier: Apache-2.0
"""Live evaluation of an agent revision (S40-02d, ADR 0071; ADR 0020 level 2 for one revision).

The suite's `live` cases run as the trials the console already knows (`AgentTrial`: one capped run of a
spec on a sample input, off the board) with the revision's own spec. Nothing here is workflow code. The
outcome is not stored: it is read from the trial runs — a case passes when its run succeeded, the result
has the status the case expects, and its summary contains every `must_contain` and none of
`must_not_contain`. An evaluation that could not run (no model to call) says `not_run`, never `passed`.
"""

from __future__ import annotations

from typing import Any

from choregos_core import utcnow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import Agent, AgentEvaluation, AgentVersion, Project, Run
from ..errors import conflict, not_found
from ..rbac import Principal
from ..schemas import AgentEvalCase, AgentEvaluationCase, AgentSpec
from ..schemas import AgentEvaluation as AgentEvaluationDto
from ..temporal import get_temporal, trial_run_id, trial_workflow_id
from .routage import nouveau_ticket_d_essai

MAX_LIVE_CASES = 10
#: What one case may spend unless it says less (the trial's own default and cap, S32-03).
CASE_DEFAULT_USD = 0.25
FINISHED = ("succeeded", "failed", "timed_out", "cancelled")


def live_cases(spec: AgentSpec) -> list[AgentEvalCase]:
    return [case for case in spec.evals if case.live]


def case_budget(case: AgentEvalCase) -> float:
    return round(min(0.5, case.max_cost_usd or CASE_DEFAULT_USD), 4)


def judge(case: AgentEvalCase, status: str, result: dict[str, Any] | None) -> tuple[bool, str | None]:
    """Did the run do what the case expects? The second value says why not."""
    if status != "succeeded" and case.expect_status == "done":
        return False, f"the run ended {status}"
    outcome = result or {}
    got = str(outcome.get("status") or "")
    if got != case.expect_status:
        return False, f"expected the result status `{case.expect_status}`, got `{got or 'none'}`"
    text = str(outcome.get("summary") or "")
    missing = [m for m in case.must_contain if m not in text]
    forbidden = [f for f in case.must_not_contain if f in text]
    if missing or forbidden:
        parts = [*(f"missing “{m}”" for m in missing), *(f"contains “{f}”" for f in forbidden)]
        return False, "; ".join(parts)
    return True, None


async def start(
    session: AsyncSession,
    principal: Principal,
    agent: Agent,
    project: Project,
    version: int,
    max_eval_usd: float,
) -> AgentEvaluation:
    """Start one trial per live case of the revision; refuse what would spend more than `max_eval_usd`."""
    row = (
        await session.execute(
            select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.version == version)
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Agent version", f"{agent.slug}@{version}")
    spec = AgentSpec.model_validate(row.spec)
    cases = live_cases(spec)
    if not cases:
        raise conflict(f"v{version} has no live case in its eval suite: mark a case `live` first")
    if len(cases) > MAX_LIVE_CASES:
        raise conflict(f"a live evaluation runs at most {MAX_LIVE_CASES} cases; v{version} has {len(cases)}")
    budgets = [case_budget(case) for case in cases]
    total = round(sum(budgets), 4)
    if total > max_eval_usd:
        raise conflict(
            f"this evaluation could spend {total:.2f} $ ({len(cases)} cases), above the policy's "
            f"{max_eval_usd:.2f} $: lower a case's `max_cost_usd` or raise `max_eval_usd`"
        )
    settings = get_settings()
    can_run = settings.fakes or bool(settings.gateway_master_key)
    started: list[dict[str, Any]] = []
    for case, budget in zip(cases, budgets, strict=True):
        item = await nouveau_ticket_d_essai(
            session,
            project,
            title=case.input.title or case.name,
            body=case.input.body,
            created_by=principal.email,
        )
        if can_run:
            await get_temporal().start_trial(
                trial_workflow_id(item.id),
                {
                    "project_id": project.id,
                    "work_item_id": item.id,
                    "spec": row.spec,
                    "budget_usd": budget,
                },
            )
            item.temporal_wf_id = trial_workflow_id(item.id)
        started.append(
            {
                "name": case.name,
                "work_item_id": item.id,
                "run_id": trial_run_id(item.id),
                "budget_usd": budget,
            }
        )
    evaluation = AgentEvaluation(
        org_id=agent.org_id,
        agent_id=agent.id,
        version=version,
        project_id=project.id,
        status="running" if can_run else "not_run",
        budget_usd=total,
        started_by=principal.email,
        cases=started,
    )
    session.add(evaluation)
    await session.flush()
    return evaluation


def _threshold(floor: float | None) -> float:
    return 1.0 if floor is None else floor


async def read(
    session: AsyncSession,
    evaluation: AgentEvaluation,
    project_slug: str,
    spec: AgentSpec,
    floor: float | None,
) -> AgentEvaluationDto:
    """The evaluation as it stands: each case judged from its run. `floor` is the policy's minimum score
    (1.0, every case, when none is set): it decides `passed` or `failed` once every case has finished."""
    cases = live_cases(spec)
    by_name = {case.name: case for case in cases}
    runs = {
        run.id: run
        for run in (
            await session.execute(select(Run).where(Run.id.in_([c["run_id"] for c in evaluation.cases])))
        ).scalars()
    }
    out: list[AgentEvaluationCase] = []
    for entry in evaluation.cases:
        case = by_name.get(entry["name"])
        run = runs.get(entry["run_id"])
        base = {
            "name": entry["name"],
            "run_id": entry["run_id"],
            "budget_usd": float(entry.get("budget_usd", 0.0)),
        }
        if evaluation.status == "not_run":
            out.append(
                AgentEvaluationCase(**base, status="not_run", reason="no model to call: no gateway key")
            )
        elif run is None or run.status not in FINISHED or case is None:
            out.append(AgentEvaluationCase(**base, status="running"))
        else:
            ok, why = judge(case, run.status, run.result)
            out.append(
                AgentEvaluationCase(
                    **base,
                    status="passed" if ok else "failed",
                    passed=ok,
                    cost_usd=float(run.cost_usd or 0.0),
                    reason=why,
                )
            )
    finished = [c for c in out if c.status in {"passed", "failed"}]
    score: float | None = None
    status: str
    if evaluation.status == "not_run":
        status = "not_run"
    elif len(finished) < len(out):
        status = "running"
    else:
        score = round(sum(1 for c in finished if c.passed) / len(finished), 4) if finished else None
        status = "passed" if score is not None and score >= _threshold(floor) else "failed"
    if status in {"passed", "failed"} and evaluation.finished_at is None:
        evaluation.score = score
        evaluation.finished_at = utcnow()
    return AgentEvaluationDto(
        id=evaluation.id,
        version=evaluation.version,
        project=project_slug,
        status=status,
        score=score,
        budget_usd=evaluation.budget_usd,
        started_by=evaluation.started_by,
        started_at=evaluation.created_at,
        cases=out,
    )


async def latest_finished(
    session: AsyncSession, agent: Agent, version: int, spec: AgentSpec, floor: float | None
) -> AgentEvaluationDto | None:
    """The newest evaluation of the revision that has finished (or could not run): what a gate reads."""
    rows = (
        await session.execute(
            select(AgentEvaluation, Project.slug)
            .join(Project, Project.id == AgentEvaluation.project_id)
            .where(AgentEvaluation.agent_id == agent.id, AgentEvaluation.version == version)
            .order_by(AgentEvaluation.created_at.desc(), AgentEvaluation.id.desc())
        )
    ).tuples()
    for evaluation, slug in rows:
        read_ = await read(session, evaluation, slug, spec, floor)
        if read_.status != "running":
            return read_
    return None
