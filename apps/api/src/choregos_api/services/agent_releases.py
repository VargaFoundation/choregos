# SPDX-License-Identifier: Apache-2.0
"""Release channels of an agent (S40-02, ADR 0071): production, testing, canary, rollback.

A revision is an `agent_versions` row: immutable, numbered. An agent has two pointers at revisions,
`stable_version` (production) and `candidate_version` (testing). "Publish" saves the next revision;
"release" moves a pointer. The pure functions come first (policy, canary bucket, which revision a
project is served, the kind of a move); the database functions after.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from choregos_core import utcnow
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Agent, AgentRelease, AgentVersion, Organization, Project, ProjectAgent, Run
from ..deps import _aware
from ..errors import ApiError, conflict, forbidden, not_found, unprocessable
from ..fraicheur import age_de_l_authentification, exiger_une_authentification_fraiche
from ..greffons import DemandeDeGeste, controler
from ..rbac import Permission, Principal
from ..schemas import (
    AgentChannelStats,
    AgentExposedProject,
    AgentExposure,
    AgentReleaseCheck,
    AgentReleaseCreate,
    AgentReleaseDecision,
    AgentReleaseDto,
    AgentReleasePolicy,
    AgentReleaseReadiness,
    AgentReleaseVote,
    AgentSpec,
)
from .agent_evals import evaluate, suite_for
from .agent_governance import PERSONAL, is_admin
from .agent_live_evals import latest_finished
from .hosted_builds import build_row, ensure_build, names_key, zip_build_key

FINISHED = ("succeeded", "failed", "timed_out", "cancelled")
PENDING = "pending_approval"
PRODUCTION_WINDOW_DAYS = 30
#: Checks about the caller, not about the revision: enforced as 403 / 401, not as a 409.
CALLER_CHECKS = frozenset({"human_session", "fresh_authentication"})


# ───────────────────────────── pure rules ─────────────────────────────


def policy_of(agent: Agent) -> AgentReleasePolicy:
    """The agent's release policy; NULL in the database means the defaults."""
    return AgentReleasePolicy.model_validate(agent.release_policy or {})


def canary_bucket(agent_id: str, work_item_id: str) -> int:
    """0-99, stable for one `(agent, work item)`: every stage and every retry of a work item gets the
    same revision, and a canary is reproducible from the two ids."""
    return int(hashlib.sha256(f"{agent_id}:{work_item_id}".encode()).hexdigest()[:8], 16) % 100


def in_canary(agent: Agent, work_item_id: str) -> bool:
    return canary_bucket(agent.id, work_item_id) < (agent.candidate_percent or 0)


def served(
    agent: Agent, *, pin: ProjectAgent | None, project_channel: str, work_item_id: str | None
) -> tuple[int | None, str, str | None]:
    """The revision a project is served, with why: `(version, source, channel)`.

    Steps 2 to 6 of the resolution order (step 1, an explicit `slug@N`, is the resolver's). A `None`
    version means the agent has no production pointer and the resolver reads its latest revision.
    """
    candidate = agent.candidate_version
    if pin is not None and pin.version is not None:
        return pin.version, "pin", None
    if pin is not None and pin.channel is not None:
        if pin.channel == "candidate" and candidate is not None:
            return candidate, "pin_channel", "candidate"
        return agent.stable_version, "pin_channel", "stable"
    if project_channel == "candidate" and candidate is not None:
        return candidate, "project_channel", "candidate"
    if (
        candidate is not None
        and (agent.candidate_percent or 0) > 0
        and work_item_id is not None
        and in_canary(agent, work_item_id)
    ):
        return candidate, "canary", "candidate"
    if agent.stable_version is None:
        return None, "latest", None
    return agent.stable_version, "stable", "stable"


def kind_for(
    channel: str, version: int | None, stable_version: int | None, *, was_in_production: bool
) -> str:
    """`withdraw`, `rollback` (production goes back to a revision that was there before), or `promote`."""
    if version is None and channel == "candidate":
        return "withdraw"
    if (
        channel == "stable"
        and version is not None
        and stable_version is not None
        and version < stable_version
        and was_in_production
    ):
        return "rollback"
    return "promote"


def _percent(agent: Agent, body: AgentReleaseCreate) -> int:
    """The share a candidate release leaves: the one asked, else the current one (0 for a new candidate)."""
    if body.percent is not None:
        return body.percent
    return (agent.candidate_percent or 0) if agent.candidate_version is not None else 0


def _lower(old: float | None, new: float | None) -> bool:
    """A threshold that is dropped or lowered."""
    return old is not None and (new is None or new < old)


def loosens(before: AgentReleasePolicy, after: AgentReleasePolicy) -> bool:
    """Does `after` let a revision reach production on less than `before` asked?"""
    if after.on_publish == "stable" and before.on_publish != "stable":
        return True
    old, new = before.stable, after.stable
    if old.require_candidate and not new.require_candidate:
        return True
    if new.min_candidate_runs < old.min_candidate_runs or new.step_up_minutes > old.step_up_minutes:
        return True
    if _lower(old.min_live_score, new.min_live_score) or _lower(
        before.candidate.min_live_score, after.candidate.min_live_score
    ):
        return True
    if new.approvals < old.approvals or (old.separation_of_duties and not new.separation_of_duties):
        return True
    if old.min_success_rate is not None and (
        new.min_success_rate is None or new.min_success_rate < old.min_success_rate
    ):
        return True
    return old.min_soak_hours is not None and (
        new.min_soak_hours is None or new.min_soak_hours < old.min_soak_hours
    )


# ───────────────────────────── statistics ─────────────────────────────


async def _stats(session: AsyncSession, agent: Agent, version: int, since: Any | None) -> AgentChannelStats:
    query = (
        select(Run.status, func.count())
        .join(Project, Project.id == Run.project_id)
        .where(
            Run.agent_slug == agent.slug,
            Run.agent_version == version,
            Project.org_id == agent.org_id,
            Run.status.in_(FINISHED),
        )
        .group_by(Run.status)
    )
    if since is not None:
        query = query.where(Run.created_at >= since)
    counts = {status: int(n) for status, n in (await session.execute(query)).all()}
    succeeded = counts.get("succeeded", 0)
    finished = sum(counts.values())
    return AgentChannelStats(
        version=version,
        runs=finished,
        succeeded=succeeded,
        failed=finished - succeeded,
        success_rate=round(succeeded / finished, 4) if finished else None,
        since=since,
    )


async def candidate_stats(session: AsyncSession, agent: Agent) -> AgentChannelStats | None:
    """Finished runs of the candidate since it became the candidate."""
    if agent.candidate_version is None:
        return None
    return await _stats(session, agent, agent.candidate_version, agent.candidate_since)


async def production_stats(session: AsyncSession, agent: Agent) -> AgentChannelStats | None:
    """Finished runs of the production revision over the last 30 days."""
    if agent.stable_version is None:
        return None
    since = utcnow() - timedelta(days=PRODUCTION_WINDOW_DAYS)
    return await _stats(session, agent, agent.stable_version, since)


async def previously_in_production(session: AsyncSession, agent: Agent, version: int) -> bool:
    """A rollback restores an approved state: the revision was in production before, through a release
    in the history, or under the old rule (every version up to the migration's baseline was "latest")."""
    rows = (
        await session.execute(
            select(AgentRelease.kind, AgentRelease.version).where(
                AgentRelease.agent_id == agent.id,
                AgentRelease.channel == "stable",
                AgentRelease.status == "done",
                AgentRelease.version.is_not(None),
            )
        )
    ).all()
    return any(v == version or (kind == "baseline" and v is not None and v >= version) for kind, v in rows)


async def production_versions(session: AsyncSession, agent: Agent) -> list[int]:
    """Every revision a rollback could go back to."""
    candidates = (
        await session.execute(select(AgentVersion.version).where(AgentVersion.agent_id == agent.id))
    ).scalars()
    return sorted([v for v in candidates if await previously_in_production(session, agent, v)])


# ───────────────────────────── the checks ─────────────────────────────


def _check(
    name: str, passed: bool | None, detail: str | None = None, *, required: bool = True
) -> AgentReleaseCheck:
    return AgentReleaseCheck(name=name, required=required, passed=passed, detail=detail)


def is_human_session(principal: Principal) -> bool:
    return principal.kind == "user" and principal.authentifie_le is not None


def _gated(agent: Agent, channel: str, kind: str, percent: int) -> bool:
    """A move that needs a person: production, or a canary share, of an agent that is not personal."""
    return kind != "withdraw" and agent.scope != PERSONAL and (channel == "stable" or percent > 0)


async def checks_for(
    session: AsyncSession,
    agent: Agent,
    *,
    channel: str,
    version: int | None,
    kind: str,
    percent: int,
    principal: Principal,
    now: float | None = None,
) -> list[AgentReleaseCheck]:
    """What releasing `version` to `channel` is checked against, in the order the console lists it.
    Nothing raises: a refusal is a check that did not pass, so that the readiness view can show it."""
    policy = policy_of(agent)
    gates = policy.stable
    checks: list[AgentReleaseCheck] = []
    if version is None:  # a withdrawal: nothing to check
        return checks
    row = (
        await session.execute(
            select(AgentVersion.spec).where(
                AgentVersion.agent_id == agent.id, AgentVersion.version == version
            )
        )
    ).scalar_one_or_none()
    spec = AgentSpec.model_validate(row or {})
    build_key = zip_build_key(spec)
    if build_key is not None:
        build = await build_row(session, agent.org_id, build_key)
        status = build.status if build is not None else "not started"
        checks.append(
            _check(
                "hosted_build",
                status == "ready",
                f"the image built from v{version}'s zip is ready"
                if status == "ready"
                else f"v{version} is built from a zip whose build is {status}: a channel takes it once built",
            )
        )
    if kind == "rollback":
        checks.append(_check("previously_in_production", True, f"v{version} was in production before"))
    else:
        production_row = None
        if agent.stable_version is not None:
            production_row = (
                await session.execute(
                    select(AgentVersion.spec).where(
                        AgentVersion.agent_id == agent.id, AgentVersion.version == agent.stable_version
                    )
                )
            ).scalar_one_or_none()
        stable_spec = AgentSpec.model_validate(production_row) if production_row is not None else None
        results = evaluate(spec, suite_for(spec, stable_spec))
        failing = [r.name for r in results if not r.passed]
        checks.append(
            _check(
                "eval_suite",
                not failing,
                f"{len(results)} case(s) pass" if not failing else f"failing: {', '.join(failing)}",
            )
        )
        if agent.stable_version is not None:
            newer = version > agent.stable_version
            checks.append(
                _check(
                    "newer_than_production",
                    newer,
                    f"v{version} is newer than production (v{agent.stable_version})"
                    if newer
                    else f"v{version} is not newer than production (v{agent.stable_version})",
                )
            )
        floor = policy.candidate.min_live_score if channel == "candidate" else gates.min_live_score
        if floor is not None:
            checks.append(await _live_check(session, agent, version, spec, floor))
        if channel == "stable":
            checks += await _candidate_gate_checks(session, agent, version, gates)
            if gates.approvals > 0:
                checks.append(
                    _check(
                        "approvals",
                        None,
                        f"needs {gates.approvals} approval(s) from other people before production moves",
                        required=False,
                    )
                )
    if _gated(agent, channel, kind, percent):
        human = is_human_session(principal)
        session_required = policy.on_publish != "stable"
        checks.append(
            _check(
                "human_session",
                human if (human or session_required) else None,
                "a person in a signed-in session"
                if human
                else "a release to production is made in a human session, not with an API token",
                required=session_required,
            )
        )
        age = age_de_l_authentification(principal, now)
        fresh = age is not None and age <= gates.step_up_minutes * 60
        checks.append(
            _check(
                "fresh_authentication",
                fresh if (human or session_required) else None,
                f"needs a sign-in less than {gates.step_up_minutes} min old"
                if not fresh
                else f"signed in {(age or 0) // 60} min ago",
                required=human or session_required,
            )
        )
    return checks


async def _live_check(
    session: AsyncSession, agent: Agent, version: int, spec: AgentSpec, floor: float
) -> AgentReleaseCheck:
    """The latest finished live evaluation of the revision reaches the policy's minimum score. Nothing
    evaluated, or an evaluation that could not run, is a failure — never a pass (ADR 0020)."""
    latest = await latest_finished(session, agent, version, spec, floor)
    if latest is None:
        return _check("live_eval", False, f"no live evaluation of v{version} has finished: run one")
    if latest.status == "not_run" or latest.score is None:
        return _check("live_eval", False, "the last live evaluation could not run: no model to call")
    return _check(
        "live_eval",
        latest.score >= floor,
        f"score {latest.score:.0%} against {floor:.0%} required",
    )


async def _candidate_gate_checks(
    session: AsyncSession, agent: Agent, version: int, gates: Any
) -> list[AgentReleaseCheck]:
    needs_candidate = (
        gates.require_candidate
        or gates.min_candidate_runs > 0
        or gates.min_success_rate is not None
        or gates.min_soak_hours is not None
    )
    is_candidate = agent.candidate_version == version
    checks = [
        _check(
            "is_candidate",
            is_candidate,
            f"v{version} is the candidate"
            if is_candidate
            else f"production only takes the current candidate (testing runs "
            f"{'v' + str(agent.candidate_version) if agent.candidate_version else 'nothing'})",
            required=needs_candidate,
        )
    ]
    if not needs_candidate:
        return checks
    stats = await candidate_stats(session, agent) if is_candidate else None
    if gates.min_candidate_runs > 0:
        runs = stats.runs if stats else 0
        checks.append(
            _check(
                "candidate_runs",
                runs >= gates.min_candidate_runs,
                f"{runs} of {gates.min_candidate_runs} finished runs",
            )
        )
    if gates.min_success_rate is not None:
        rate = stats.success_rate if stats else None
        checks.append(
            _check(
                "candidate_success_rate",
                rate is not None and rate >= gates.min_success_rate,
                "no finished run yet"
                if rate is None
                else f"{rate:.0%} against {gates.min_success_rate:.0%} required",
            )
        )
    if gates.min_soak_hours is not None:
        since = agent.candidate_since
        hours = (utcnow() - _aware(since)).total_seconds() / 3600 if (since and is_candidate) else 0.0
        checks.append(
            _check(
                "soak", hours >= gates.min_soak_hours, f"{hours:.1f} of {gates.min_soak_hours} h in testing"
            )
        )
    return checks


def failed_checks(checks: list[AgentReleaseCheck]) -> list[AgentReleaseCheck]:
    """The required checks about the revision that did not pass (the caller's own are 403 / 401)."""
    return [c for c in checks if c.required and c.passed is not True and c.name not in CALLER_CHECKS]


async def _plan(session: AsyncSession, agent: Agent, body: AgentReleaseCreate) -> tuple[int | None, str, int]:
    """`(version, kind, percent)` of the move asked, after the cheap refusals."""
    version = body.version
    if body.channel == "candidate" and version is None:
        if agent.candidate_version is None:
            raise conflict("nothing is in testing: there is no candidate to withdraw")
        return None, "withdraw", 0
    if body.channel == "stable" and version is None:
        if agent.candidate_version is None:
            raise conflict("nothing is in testing: name the revision to release to production")
        version = agent.candidate_version
    assert version is not None
    exists = (
        await session.execute(
            select(AgentVersion.id).where(AgentVersion.agent_id == agent.id, AgentVersion.version == version)
        )
    ).first()
    if exists is None:
        raise not_found("Agent version", f"{agent.slug}@{version}")
    if body.channel == "stable":
        if version == agent.stable_version:
            raise conflict(f"v{version} is already in production")
        was = await previously_in_production(session, agent, version)
        kind = kind_for("stable", version, agent.stable_version, was_in_production=was)
        return version, kind, 0
    return version, "promote", _percent(agent, body)


async def readiness(
    session: AsyncSession, principal: Principal, agent: Agent, version: int, channel: str
) -> AgentReleaseReadiness:
    """What a release of `version` to `channel` would check, without doing it."""
    body = AgentReleaseCreate(channel=channel, version=version)
    planned, kind, percent = await _plan_or_state(session, agent, body)
    checks = await checks_for(
        session, agent, channel=channel, version=planned, kind=kind, percent=percent, principal=principal
    )
    return AgentReleaseReadiness(
        version=version,
        channel=channel,
        kind=kind,
        ready=not [c for c in checks if c.required and c.passed is not True],
        checks=checks,
        candidate=await candidate_stats(session, agent),
        production=await production_stats(session, agent),
    )


async def _plan_or_state(
    session: AsyncSession, agent: Agent, body: AgentReleaseCreate
) -> tuple[int | None, str, int]:
    """Like `_plan`, but a revision already in production is reported (as a promotion that fails
    `newer_than_production`) instead of refused: the readiness view describes, it does not decide."""
    try:
        return await _plan(session, agent, body)
    except ApiError as error:
        if error.status_code == 409 and body.version is not None:
            return body.version, "promote", 0
        raise


# ───────────────────────────── moving a pointer ─────────────────────────────


def _dto(row: AgentRelease, slug: str) -> AgentReleaseDto:
    return AgentReleaseDto(
        id=row.id,
        agent=slug,
        channel=row.channel,
        kind=row.kind,
        version=row.version,
        previous_version=row.previous_version,
        percent=row.percent,
        status=row.status,
        approvals_required=row.approvals_required or 0,
        decisions=[AgentReleaseVote.model_validate(d) for d in row.decisions or []],
        requested_by=row.requested_by,
        requested_at=row.created_at,
        done_at=row.done_at,
        reason=row.reason,
        checks=[AgentReleaseCheck.model_validate(c) for c in row.checks or []],
    )


def release_dto(row: AgentRelease, slug: str) -> AgentReleaseDto:
    return _dto(row, slug)


async def release(
    session: AsyncSession,
    principal: Principal,
    organisation: Organization,
    agent: Agent,
    body: AgentReleaseCreate,
) -> AgentRelease:
    """Point a channel at a revision. The caller has already been through the governance matrix."""
    if agent.lifecycle == "retired" or agent.status == "revoked":
        raise conflict(f"agent `{agent.slug}` is {'retired' if agent.lifecycle == 'retired' else 'revoked'}")
    # Serialise the moves of one agent (a no-op on SQLite, a row lock on PostgreSQL).
    await session.execute(select(Agent.id).where(Agent.id == agent.id).with_for_update())
    await session.refresh(agent)
    policy = policy_of(agent)
    previous = agent.stable_version if body.channel == "stable" else agent.candidate_version
    version, kind, percent = await _plan(session, agent, body)
    checks = await checks_for(
        session,
        agent,
        channel=body.channel,
        version=version,
        kind=kind,
        percent=percent,
        principal=principal,
    )
    failing = failed_checks(checks)
    if failing:
        raise ApiError(
            409,
            "Release checks failed",
            f"v{version} cannot go to {'production' if body.channel == 'stable' else 'testing'}: "
            f"{len(failing)} check(s) fail",
            errors=[
                {"loc": ["checks", c.name], "msg": c.detail or c.name, "type": "release_check_failed"}
                for c in failing
            ],
        )
    if _gated(agent, body.channel, kind, percent):
        if is_human_session(principal):
            exiger_une_authentification_fraiche(principal, policy.stable.step_up_minutes)
        elif policy.on_publish != "stable":
            raise forbidden("a release to production is made in a human session, not with an API token")
        await controler(
            session,
            DemandeDeGeste(
                "agent.release",
                organisation.slug,
                principal,
                {"agent": agent.slug, "channel": body.channel, "version": version, "kind": kind},
            ),
        )
    now = utcnow()
    approvals = policy.stable.approvals
    if body.channel == "stable" and kind == "promote" and approvals > 0:
        assert version is not None
        await _refuse_a_second_request(session, agent)
        return await _open_request(
            session,
            principal,
            agent,
            version,
            previous=agent.stable_version,
            requested_by=principal.email,
            reason=body.reason,
            checks=checks,
            approvals=approvals,
        )
    if body.channel == "stable":
        assert version is not None
        values: dict[str, Any] = {"stable_version": version}
        candidate = agent.candidate_version
        if candidate is not None and candidate <= version:
            values |= {"candidate_version": None, "candidate_percent": 0, "candidate_since": None}
        moved = await session.execute(
            update(Agent)
            .where(Agent.id == agent.id, Agent.stable_version.is_not_distinct_from(previous))
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if moved.rowcount != 1:  # type: ignore[attr-defined]
            raise conflict("another release has just been made: reload and try again")
        recorded_percent = None
    elif kind == "withdraw":
        await session.execute(
            update(Agent)
            .where(Agent.id == agent.id)
            .values(candidate_version=None, candidate_percent=0, candidate_since=None)
            .execution_options(synchronize_session=False)
        )
        recorded_percent = None
    else:
        await session.execute(
            update(Agent)
            .where(Agent.id == agent.id)
            .values(
                candidate_version=version,
                candidate_percent=percent,
                candidate_since=now if version != agent.candidate_version else agent.candidate_since,
            )
            .execution_options(synchronize_session=False)
        )
        recorded_percent = percent
    await session.refresh(agent)
    row = AgentRelease(
        org_id=agent.org_id,
        agent_id=agent.id,
        channel=body.channel,
        kind=kind,
        version=version,
        previous_version=previous,
        percent=recorded_percent,
        status="done",
        requested_by=principal.email,
        reason=body.reason,
        checks=[c.model_dump(mode="json") for c in checks],
        done_at=now,
    )
    session.add(row)
    await session.flush()
    await record(
        session,
        principal,
        "agent.release",
        org_id=agent.org_id,
        target_type="agent",
        target_id=agent.id,
        channel=body.channel,
        version=version,
        previous_version=previous,
        kind=kind,
        percent=recorded_percent,
        reason=body.reason,
    )
    return row


async def pending_release(session: AsyncSession, agent: Agent) -> AgentRelease | None:
    """The release waiting for approval, if any (at most one per agent)."""
    return (
        await session.execute(
            select(AgentRelease).where(AgentRelease.agent_id == agent.id, AgentRelease.status == PENDING)
        )
    ).scalar_one_or_none()


async def _refuse_a_second_request(session: AsyncSession, agent: Agent) -> None:
    waiting = await pending_release(session, agent)
    if waiting is not None:
        raise conflict(
            f"v{waiting.version} already waits for approval to go to production: "
            "approve, reject or cancel it first"
        )


async def _open_request(  # noqa: PLR0913 - one request row, field by field, keyword-only
    session: AsyncSession,
    principal: Principal | None,
    agent: Agent,
    version: int,
    *,
    previous: int | None,
    requested_by: str | None,
    reason: str | None,
    checks: list[AgentReleaseCheck],
    approvals: int,
) -> AgentRelease:
    """A promotion that waits for other people: production does not move until enough approve."""
    row = AgentRelease(
        org_id=agent.org_id,
        agent_id=agent.id,
        channel="stable",
        kind="promote",
        version=version,
        previous_version=previous,
        status=PENDING,
        approvals_required=approvals,
        decisions=[],
        requested_by=requested_by,
        reason=reason,
        checks=[c.model_dump(mode="json") for c in checks],
    )
    session.add(row)
    await session.flush()
    await record(
        session,
        principal,
        "agent.release.request",
        org_id=agent.org_id,
        target_type="agent",
        target_id=agent.id,
        release_id=row.id,
        version=version,
        previous_version=previous,
        approvals_required=approvals,
        reason=reason,
    )
    return row


async def decide_release(
    session: AsyncSession,
    principal: Principal,
    organisation: Organization,
    agent: Agent,
    release_id: str,
    body: AgentReleaseDecision,
) -> AgentRelease:
    """Approve, reject or cancel a release that waits (the caller holds the release right).

    The rules are those of a governed action's decision (ADR 0035): a person in a session, a fresh sign-in
    to approve, neither the requester nor the revision's author when `separation_of_duties`, never twice,
    and a compare-and-set on the pointer that production moves from."""
    policy = policy_of(agent)
    gates = policy.stable
    row = (
        await session.execute(
            select(AgentRelease)
            .where(AgentRelease.id == release_id, AgentRelease.agent_id == agent.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Agent release", release_id)
    if not is_human_session(principal):
        raise forbidden("a decision is made in a human session, not with an API token")
    if row.status != PENDING:
        raise conflict(f"the release is already {row.status}")
    me = principal.email.lower()
    now = utcnow()
    decisions = list(row.decisions or [])
    if body.decision == "cancel":
        if (row.requested_by or "").lower() != me and not is_admin(principal, organisation.slug):
            raise forbidden("only the requester or an org_admin cancels a release request")
        age: int | None = age_de_l_authentification(principal)
    elif body.decision == "reject":
        if not (body.reason or "").strip():
            raise unprocessable("a rejection must say why")
        age = age_de_l_authentification(principal)
    else:
        age = exiger_une_authentification_fraiche(principal, gates.step_up_minutes)
        if gates.separation_of_duties:
            author = (
                await session.execute(
                    select(AgentVersion.created_by).where(
                        AgentVersion.agent_id == agent.id, AgentVersion.version == row.version
                    )
                )
            ).scalar_one_or_none()
            if me in {(row.requested_by or "").lower(), (author or "").lower()}:
                raise unprocessable(
                    "separation of duties: neither the person who asked for this release nor the author "
                    "of the revision approves it"
                )
        if any(d.get("by", "").lower() == me and d.get("decision") == "approve" for d in decisions):
            raise conflict("you have already approved this release")
        await controler(
            session,
            DemandeDeGeste(
                "agent.release",
                organisation.slug,
                principal,
                {
                    "agent": agent.slug,
                    "channel": "stable",
                    "version": row.version,
                    "kind": row.kind,
                    "decision": "approve",
                },
            ),
        )
    decisions.append(
        AgentReleaseVote(
            by=principal.email, decision=body.decision, reason=body.reason, at=now, auth_age_seconds=age
        ).model_dump(mode="json")
    )
    row.decisions = decisions
    approved = {d["by"].lower() for d in decisions if d.get("decision") == "approve"}
    if body.decision != "approve":
        row.status = "rejected" if body.decision == "reject" else "cancelled"
    elif len(approved) >= row.approvals_required:
        await _apply(session, agent, row, principal, now)
    await session.flush()
    await record(
        session,
        principal,
        "agent.release.decide",
        org_id=agent.org_id,
        target_type="agent",
        target_id=agent.id,
        release_id=row.id,
        decision=body.decision,
        reason=body.reason,
        status=row.status,
        auth_age_seconds=age,
    )
    return row


async def _apply(
    session: AsyncSession, agent: Agent, row: AgentRelease, principal: Principal, now: Any
) -> None:
    """The last approval is in: re-check, then move production from where the request saw it."""
    await session.refresh(agent)
    assert row.version is not None
    if agent.stable_version != row.previous_version:
        # Production moved meanwhile (a rollback, another release): the request is stale. The status
        # must survive the refusal, so it is committed before the 409 is raised.
        row.status = "superseded"
        await session.flush()
        await session.commit()
        raise conflict(
            f"production moved meanwhile (v{agent.stable_version}, not v{row.previous_version}): "
            "this request is superseded; ask again"
        )
    checks = await checks_for(
        session,
        agent,
        channel="stable",
        version=row.version,
        kind="promote",
        percent=0,
        principal=principal,
    )
    failing = failed_checks(checks)
    if failing:
        raise ApiError(
            409,
            "Release checks failed",
            f"v{row.version} no longer passes {len(failing)} check(s): it cannot go to production",
            errors=[
                {"loc": ["checks", c.name], "msg": c.detail or c.name, "type": "release_check_failed"}
                for c in failing
            ],
        )
    values: dict[str, Any] = {"stable_version": row.version}
    if agent.candidate_version is not None and agent.candidate_version <= row.version:
        values |= {"candidate_version": None, "candidate_percent": 0, "candidate_since": None}
    moved = await session.execute(
        update(Agent)
        .where(Agent.id == agent.id, Agent.stable_version.is_not_distinct_from(row.previous_version))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if moved.rowcount != 1:  # type: ignore[attr-defined]
        raise conflict("another release has just been made: reload and try again")
    row.status = "done"
    row.done_at = now
    await session.refresh(agent)
    await record(
        session,
        principal,
        "agent.release",
        org_id=agent.org_id,
        target_type="agent",
        target_id=agent.id,
        channel="stable",
        version=row.version,
        previous_version=row.previous_version,
        kind=row.kind,
        release_id=row.id,
        approved_by=[d["by"] for d in row.decisions if d.get("decision") == "approve"],
    )


async def list_releases(
    session: AsyncSession, visible: Any, status: str | None, limit: int
) -> list[tuple[AgentRelease, str]]:
    """Releases of the agents the caller may see (`visible`: the SQL clause of the agents' list), with
    their agent's slug, newest first — the approver's inbox with `status=pending_approval`."""
    query = (
        select(AgentRelease, Agent.slug)
        .join(Agent, Agent.id == AgentRelease.agent_id)
        .where(visible)
        .order_by(AgentRelease.created_at.desc(), AgentRelease.id.desc())
        .limit(limit)
    )
    if status is not None:
        query = query.where(AgentRelease.status == status)
    return [(row, slug) for row, slug in (await session.execute(query)).tuples()]


async def after_publish(
    session: AsyncSession, principal: Principal | None, agent: Agent, version: int, author: str | None
) -> None:
    """Called by every site that inserts an `AgentVersion`. A first revision is production; a later one
    follows the agent's `on_publish`: `stable` moves production (its eval suite has just passed), `candidate`
    sends it to testing, `none` leaves both pointers alone."""
    now = utcnow()
    policy = policy_of(agent)
    # A first revision is production whatever the policy: there is nothing else to serve.
    target = "stable" if version == 1 else policy.on_publish
    if agent.stable_version is None and version > 1 and target != "stable":
        # Defensive: an agent created by a path that sets no production pointer keeps serving its
        # previous latest revision.
        agent.stable_version = version - 1
        session.add(
            AgentRelease(
                org_id=agent.org_id,
                agent_id=agent.id,
                channel="stable",
                kind="baseline",
                version=version - 1,
                status="done",
                requested_by="system",
                checks=[],
                done_at=now,
            )
        )
    if not await _built(session, agent, version):
        # A zip whose image is not built yet: the move waits for its build (`release_built`).
        return
    if target == "stable" and version > 1 and policy.stable.approvals > 0:
        # The organisation wants people to approve what reaches production, publication included:
        # the revision is saved, production does not move until enough of them approve.
        waiting = await pending_release(session, agent)
        if waiting is not None:  # a newer revision replaces the request of an older one
            waiting.status = "superseded"
        await _open_request(
            session,
            principal,
            agent,
            version,
            previous=agent.stable_version,
            requested_by=author,
            reason="saved with on_publish: stable",
            checks=[],
            approvals=policy.stable.approvals,
        )
        return
    if target == "stable":
        previous = agent.stable_version
        agent.stable_version = version
        if agent.candidate_version is not None and agent.candidate_version <= version:
            agent.candidate_version, agent.candidate_percent, agent.candidate_since = None, 0, None
        channel, percent = "stable", None
    elif target == "candidate":
        previous = agent.candidate_version
        agent.candidate_version = version
        agent.candidate_since = now
        channel, percent = "candidate", agent.candidate_percent or 0
    else:
        return
    session.add(
        AgentRelease(
            org_id=agent.org_id,
            agent_id=agent.id,
            channel=channel,
            kind="publish",
            version=version,
            previous_version=previous,
            percent=percent,
            status="done",
            requested_by=author,
            checks=[],
            done_at=now,
        )
    )
    await session.flush()
    await record(
        session,
        principal,
        "agent.release",
        org_id=agent.org_id,
        target_type="agent",
        target_id=agent.id,
        channel=channel,
        version=version,
        previous_version=previous,
        kind="publish",
        via="publish",
    )


async def _built(session: AsyncSession, agent: Agent, version: int) -> bool:
    """Starts the build of a zip version (ADR 0080 §9) and says whether its image is ready; any
    other version is."""
    spec = (
        await session.execute(
            select(AgentVersion.spec).where(
                AgentVersion.agent_id == agent.id, AgentVersion.version == version
            )
        )
    ).scalar_one_or_none()
    build = await ensure_build(session, agent.org_id, spec or {})
    return build is None or build.status == "ready"


async def release_built(session: AsyncSession, org_id: str, key: str) -> list[tuple[str, int]]:
    """The moves publication deferred for a build that is now ready: for each agent, its newest
    version built from this key goes where `on_publish` sends it, unless production or testing
    already took a newer one, or a newer one awaits approval. `(slug, version)` of each move."""
    rows = await session.execute(
        select(Agent, AgentVersion.version, AgentVersion.spec)
        .join(Agent, Agent.id == AgentVersion.agent_id)
        .where(AgentVersion.org_id == org_id, names_key(key))
        .order_by(AgentVersion.version.desc())
    )
    newest: dict[str, tuple[Agent, int]] = {}
    for agent, version, spec in rows.tuples():
        if zip_build_key(spec) == key and agent.id not in newest:
            newest[agent.id] = (agent, version)
    moved: list[tuple[str, int]] = []
    for agent, version in newest.values():
        if agent.lifecycle == "retired" or agent.status == "revoked":
            continue
        target = "stable" if version == 1 else policy_of(agent).on_publish
        pointer = agent.stable_version if target == "stable" else agent.candidate_version
        waiting = await pending_release(session, agent)
        if (pointer is not None and pointer >= version) or (
            waiting is not None and (waiting.version or 0) >= version
        ):
            continue
        await after_publish(session, None, agent, version, "system:hosted-build")
        moved.append((agent.slug, version))
    return moved


async def production_spec(session: AsyncSession, agent: Agent) -> dict[str, Any] | None:
    """The spec of the revision in production (the latest for an agent with no production pointer)."""
    query = select(AgentVersion.spec).where(AgentVersion.agent_id == agent.id)
    if agent.stable_version is not None:
        query = query.where(AgentVersion.version == agent.stable_version)
    else:
        query = query.order_by(AgentVersion.version.desc()).limit(1)
    return (await session.execute(query)).scalar_one_or_none()


async def production_spec_of(
    session: AsyncSession, org_id: str, slug: str, *, active_only: bool = False
) -> dict[str, Any] | None:
    """`production_spec` for an agent named by its slug; `None` when there is no such agent."""
    query = select(Agent).where(Agent.org_id == org_id, Agent.slug == slug)
    if active_only:
        query = query.where(Agent.status == "active")
    agent = (await session.execute(query)).scalar_one_or_none()
    return None if agent is None else await production_spec(session, agent)


async def exposure(
    session: AsyncSession, principal: Principal, organisation: Organization, agent: Agent
) -> AgentExposure:
    """Production, testing, and the projects the caller may see that chose their own revision."""
    projects: list[AgentExposedProject] = []
    pins = (
        await session.execute(
            select(ProjectAgent, Project.slug)
            .join(Project, Project.id == ProjectAgent.project_id)
            .where(ProjectAgent.agent_id == agent.id)
            .order_by(Project.slug)
        )
    ).tuples()
    pinned: set[str] = set()
    for pin, slug in pins:
        pinned.add(pin.project_id)
        if not principal.can(Permission.PROJECT_READ, organisation.slug, slug):
            continue
        version, source, channel = served(agent, pin=pin, project_channel="stable", work_item_id=None)
        projects.append(
            AgentExposedProject(
                project=slug,
                source=source,
                channel=channel,
                version=version if version is not None else agent.stable_version or 0,
            )
        )
    if agent.candidate_version is not None:
        following = (
            await session.execute(
                select(Project.id, Project.slug)
                .where(Project.org_id == agent.org_id, Project.agent_channel == "candidate")
                .order_by(Project.slug)
            )
        ).tuples()
        for project_id, slug in following:
            if project_id in pinned or not principal.can(Permission.PROJECT_READ, organisation.slug, slug):
                continue
            projects.append(
                AgentExposedProject(
                    project=slug,
                    source="project_channel",
                    channel="candidate",
                    version=agent.candidate_version,
                )
            )
    return AgentExposure(
        stable_version=agent.stable_version,
        candidate_version=agent.candidate_version,
        candidate_percent=agent.candidate_percent or 0,
        projects=sorted(projects, key=lambda p: p.project),
    )


async def history(session: AsyncSession, agent: Agent, limit: int) -> list[AgentRelease]:
    rows = await session.execute(
        select(AgentRelease)
        .where(AgentRelease.agent_id == agent.id)
        .order_by(AgentRelease.created_at.desc(), AgentRelease.id.desc())
        .limit(limit)
    )
    return list(rows.scalars())


__all__ = [
    "CALLER_CHECKS",
    "after_publish",
    "canary_bucket",
    "candidate_stats",
    "checks_for",
    "decide_release",
    "exposure",
    "failed_checks",
    "history",
    "in_canary",
    "is_human_session",
    "kind_for",
    "list_releases",
    "loosens",
    "pending_release",
    "policy_of",
    "previously_in_production",
    "production_spec",
    "production_spec_of",
    "production_stats",
    "production_versions",
    "readiness",
    "release",
    "release_built",
    "release_dto",
    "served",
]
