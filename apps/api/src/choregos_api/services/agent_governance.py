# SPDX-License-Identifier: Apache-2.0
"""Who may do what with an agent (S39-09, ADR 0061): one table, one function.

An agent has a scope: `platform` (a common agent installed from the catalogue), `org`, `project`
(one project's) or `personal` (one user's). `decide` answers every question the routers ask, from the
principal's roles alone; it touches no database, so the whole matrix is testable cell by cell.

Roles: A = `org_admin`; O(P) = `project_owner` of project P (the project's maintainer; an organisation-
wide `project_owner` is one everywhere); D = anyone who holds `agent:create_personal` in the organisation
or in one of its projects (developer and above); M(P) = holds a role in P (an organisation role counts);
U = the personal agent's owner.

| action \\ scope        | platform        | org             | project P        | personal (owner U)      |
|-----------------------|-----------------|-----------------|------------------|-------------------------|
| see                   | any member      | any member      | M(P), A          | U, A                    |
| create                | A (catalogue)   | A               | O(P), A          | D (becomes U), A        |
| publish, edit, life   | A               | A               | O(P), A          | U, A                    |
| release (S40-02)      | A               | A               | O(P), A          | U, A                    |
| manage (suspend, ...) | A               | A               | O(P), A          | U, A                    |
| pin in project Q      | O(Q)            | O(Q)            | O(Q) with Q = P  | never (422)             |
| trial in project Q    | item:control(Q) | item:control(Q) | Q = P            | U, item:control(Q)      |
| fork into scope S     | see source and create in S (never into `platform`)                         |
| move (promote)        | never           | -> project: A   | -> org: A        | -> project P: U and     |
|                       |                 |                 |                  | O(P), or A; -> org: A   |

A refusal carries the sentence the console shows; a pin that cannot be is a 422, any other refusal a 403.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import and_, false, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..db.models import Agent, Organization, Project
from ..rbac import Permission, Principal
from ..schemas.agents import AgentPermissions

PLATFORM, ORG, PROJECT, PERSONAL = "platform", "org", "project", "personal"
SCOPES = (PLATFORM, ORG, PROJECT, PERSONAL)
CREATABLE = (ORG, PROJECT, PERSONAL)


class Action(StrEnum):
    SEE = "see"
    CREATE = "create"
    PUBLISH = "publish"
    #: Move the channels of an agent: send a revision to testing or production, roll back (S40-02).
    RELEASE = "release"
    MANAGE = "manage"
    PIN = "pin"
    TRIAL = "trial"
    FORK = "fork"
    MOVE = "move"
    #: Talk to the agent in production directly, in a project (S41-09, ADR 0080 §10).
    CONVERSE = "converse"


@dataclass(frozen=True, slots=True)
class Subject:
    """What the rules need to know of an agent: its scope, its project's slug, its owner."""

    scope: str
    project_slug: str | None = None
    owner_id: str | None = None
    slug: str = ""


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str | None = None
    #: A refusal that is about the request itself (a pin that cannot be) rather than the caller's rights.
    unprocessable: bool = False


ALLOWED = Decision(True)


def _no(reason: str, *, unprocessable: bool = False) -> Decision:
    return Decision(False, reason, unprocessable)


# ───────────────────────────── the caller's roles ─────────────────────────────


def is_member(principal: Principal, org: str) -> bool:
    """Holds a role in the organisation, or in one of its projects."""
    return org in principal.org_roles or any(key.startswith(f"{org}/") for key in principal.project_roles)


def is_admin(principal: Principal, org: str) -> bool:
    return principal.can(Permission.AGENT_MANAGE, org)


def _holds_anywhere(principal: Principal, org: str, permission: Permission) -> bool:
    if principal.can(permission, org):
        return True
    return any(
        principal.can(permission, org, key.split("/", 1)[1])
        for key in principal.project_roles
        if key.startswith(f"{org}/")
    )


def _may_create_personal(principal: Principal, org: str) -> bool:
    return _holds_anywhere(principal, org, Permission.AGENT_CREATE_PERSONAL)


def _owns_project(principal: Principal, org: str, project: str | None) -> bool:
    """Owner of that project; with no project named, of at least one."""
    if project is not None:
        return principal.can(Permission.AGENT_PROJECT_MANAGE, org, project)
    return _holds_anywhere(principal, org, Permission.AGENT_PROJECT_MANAGE)


def may_create_somewhere(principal: Principal, org: str) -> bool:
    """May create an agent at some scope of the organisation: what uploading its source asks."""
    return (
        is_admin(principal, org)
        or _may_create_personal(principal, org)
        or _owns_project(principal, org, None)
    )


def _belongs_to(principal: Principal, org: str, project: str | None) -> bool:
    return project is not None and principal.role_for(org, project) is not None


# ───────────────────────────── the table ─────────────────────────────

Rule = Callable[[Principal, str, Subject, "Request"], Decision]


@dataclass(frozen=True, slots=True)
class Request:
    project_slug: str | None = None
    target_scope: str | None = None


_FORK_HINT = "fork it into your project or your own agents to extend it"


def _see(principal: Principal, org: str, subject: Subject, _: Request) -> Decision:
    if is_admin(principal, org):
        return ALLOWED
    if subject.scope in {PLATFORM, ORG}:
        return (
            ALLOWED
            if is_member(principal, org)
            else _no(f"reading the agents of {org} requires being a member")
        )
    if subject.scope == PROJECT:
        if _belongs_to(principal, org, subject.project_slug):
            return ALLOWED
        return _no(f"this agent belongs to project `{subject.project_slug}`, of which you are not a member")
    if subject.owner_id is not None and subject.owner_id == principal.user_id:
        return ALLOWED
    return _no("this is someone else's personal agent")


def _change(principal: Principal, org: str, subject: Subject, _: Request) -> Decision:
    """Publish, edit, move through its life — and manage: the same people, scope by scope."""
    if is_admin(principal, org):
        return ALLOWED
    if subject.scope == PLATFORM:
        return _no(f"`{subject.slug}` is a common agent: only an org_admin changes it; {_FORK_HINT}")
    if subject.scope == ORG:
        return _no(f"`{subject.slug}` is an organisation agent: only an org_admin changes it; {_FORK_HINT}")
    if subject.scope == PROJECT:
        if _owns_project(principal, org, subject.project_slug):
            return ALLOWED
        return _no(
            f"`{subject.slug}` belongs to project `{subject.project_slug}`: only its project_owner or an "
            f"org_admin changes it; {_FORK_HINT}"
        )
    if subject.owner_id is not None and subject.owner_id == principal.user_id:
        return ALLOWED
    return _no(f"`{subject.slug}` is someone else's personal agent")


def _pin(principal: Principal, org: str, subject: Subject, request: Request) -> Decision:
    target = request.project_slug
    if target is None:
        return _no("pinning needs a project", unprocessable=True)
    if subject.scope == PERSONAL:
        return _no(
            f"`{subject.slug}` is a personal agent: a workflow run by a team does not depend on one person's "
            "agent. Promote it to the project or fork it into the project first",
            unprocessable=True,
        )
    if subject.scope == PROJECT and subject.project_slug != target:
        return _no(
            f"`{subject.slug}` belongs to project `{subject.project_slug}` and runs only there: fork it into "
            f"`{target}` instead",
            unprocessable=True,
        )
    if principal.can(Permission.PROJECT_WRITE, org, target):
        return ALLOWED
    return _no(f"pinning an agent on `{target}` needs the project_owner role there")


def _trial(principal: Principal, org: str, subject: Subject, request: Request) -> Decision:
    target = request.project_slug
    if target is None or not principal.can(Permission.ITEM_CONTROL, org, target):
        return _no(f"trying an agent in {org}/{target} requires working on that project")
    if not (is_admin(principal, org) or _may_create_personal(principal, org)):
        return _no("trying an agent needs the developer role or above")
    if subject.scope == PROJECT and subject.project_slug != target:
        return _no(f"`{subject.slug}` belongs to project `{subject.project_slug}`: try it there")
    if subject.scope == PERSONAL and subject.owner_id != principal.user_id:
        return _no(f"`{subject.slug}` is someone else's personal agent")
    return ALLOWED


def _converse(principal: Principal, org: str, subject: Subject, request: Request) -> Decision:
    """A session is work in a project, paid by its budget: whoever drives that project's work items,
    and no one else. The agent's own reach stays its own: a project's agent only in its project, and a
    personal agent in none (ADR 0061), as for a run."""
    target = request.project_slug
    if target is None or not principal.can(Permission.ITEM_CONTROL, org, target):
        return _no(f"talking to an agent in {org}/{target} requires working on that project")
    if subject.scope == PROJECT and subject.project_slug != target:
        return _no(f"`{subject.slug}` belongs to project `{subject.project_slug}`: talk to it there")
    if subject.scope == PERSONAL:
        return _no(f"`{subject.slug}` is a personal agent: it works in no project", unprocessable=True)
    return ALLOWED


def _create_in(principal: Principal, org: str, scope: str | None, project: str | None) -> Decision:
    if is_admin(principal, org):
        return ALLOWED
    if scope == PERSONAL:
        if _may_create_personal(principal, org):
            return ALLOWED
        return _no("a personal agent needs the developer role or above")
    if scope == PROJECT:
        if _owns_project(principal, org, project):
            return ALLOWED
        where = f"project `{project}`" if project else "a project"
        return _no(f"an agent of {where} is created by its project_owner or an org_admin")
    if scope == PLATFORM:
        return _no("a common agent is installed from the catalogue by an org_admin")
    return _no("an organisation agent is created by an org_admin; create a personal agent, or fork one")


def _create(principal: Principal, org: str, _: Subject, request: Request) -> Decision:
    return _create_in(principal, org, request.target_scope, request.project_slug)


def _fork(principal: Principal, org: str, subject: Subject, request: Request) -> Decision:
    seen = _see(principal, org, subject, request)
    if not seen.allowed:
        return seen
    if request.target_scope == PLATFORM:
        return _no("nothing is forked into the common agents: they come from the catalogue")
    return _create_in(principal, org, request.target_scope, request.project_slug)


def _move(principal: Principal, org: str, subject: Subject, request: Request) -> Decision:
    target, project = request.target_scope, request.project_slug
    if subject.scope == PLATFORM:
        return _no(f"`{subject.slug}` is a common agent: it never changes scope; fork it instead")
    if target in {None, PLATFORM}:
        return _no("nothing is promoted to the common agents: they come from the catalogue")
    if target == subject.scope and (target != PROJECT or project == subject.project_slug):
        return _no(f"`{subject.slug}` is already in that scope")
    admin = is_admin(principal, org)
    if target == ORG:
        return ALLOWED if admin else _no("moving an agent to the organisation needs an org_admin")
    if target == PROJECT:
        if subject.scope == PERSONAL:
            if admin or (subject.owner_id == principal.user_id and _owns_project(principal, org, project)):
                return ALLOWED
            return _no(
                "promoting a personal agent to a project takes its owner AND that project's project_owner "
                "(or an org_admin)"
            )
        if subject.scope == ORG:
            return ALLOWED if admin else _no("moving an organisation agent into a project needs an org_admin")
        return _no(
            f"`{subject.slug}` belongs to project `{subject.project_slug}`: promote it to the organisation"
        )
    return _no("an agent is promoted, never made personal: fork it into your own agents")


TABLE: dict[Action, Rule] = {
    Action.SEE: _see,
    Action.CREATE: _create,
    Action.PUBLISH: _change,
    Action.RELEASE: _change,
    Action.MANAGE: _change,
    Action.PIN: _pin,
    Action.TRIAL: _trial,
    Action.CONVERSE: _converse,
    Action.FORK: _fork,
    Action.MOVE: _move,
}

_NOBODY = Subject(scope=ORG)


def decide(
    principal: Principal,
    org: str,
    agent: Subject | None,
    action: Action,
    *,
    project_slug: str | None = None,
    target_scope: str | None = None,
) -> Decision:
    """May `principal` do `action`? `agent` is the one concerned (none for a creation or a trial of a
    draft), `project_slug` the project pinned on, tried in, or created in, `target_scope` where a
    fork, a creation or a move lands."""
    if agent is None and action not in {Action.CREATE, Action.TRIAL}:
        raise ValueError(f"`{action}` concerns an agent")
    return TABLE[action](principal, org, agent or _NOBODY, Request(project_slug, target_scope))


def usable_in_project(scope: str, agent_project_id: str | None, slug: str, project_id: str) -> str | None:
    """Why this agent may not run in, or be named by, the workflow of that project; `None` if it may.

    Enforced where a project pins it, where a workflow naming it is published, and when a run resolves
    it — a personal agent never runs in a workflow, a project agent runs only in its project."""
    if scope == PERSONAL:
        return (
            f"agent `{slug}` is a personal agent: it never runs in a project's workflow. Promote it to the "
            "project or fork it into the project"
        )
    if scope == PROJECT and agent_project_id != project_id:
        return f"agent `{slug}` belongs to another project and runs only there. Fork it into this project"
    return None


# ───────────────────────────── from the database ─────────────────────────────


async def subject_of(session: AsyncSession, agent: Agent) -> Subject:
    slug = None
    if agent.project_id is not None:
        project = await session.get(Project, agent.project_id)
        slug = project.slug if project else None
    return Subject(scope=agent.scope, project_slug=slug, owner_id=agent.owner_id, slug=agent.slug)


async def visible_clause(session: AsyncSession, principal: Principal, organisation: Organization) -> Any:
    """The SQL condition on `Agent` for the agents the caller may see (the list's filter)."""
    org = organisation.slug
    if is_admin(principal, org):
        return true()
    if not is_member(principal, org):
        return false()
    own: ColumnElement[bool] = and_(Agent.scope == PERSONAL, Agent.owner_id == principal.user_id)
    if org in principal.org_roles:
        return or_(Agent.scope.in_([PLATFORM, ORG, PROJECT]), own)
    slugs = [key.split("/", 1)[1] for key in principal.project_roles if key.startswith(f"{org}/")]
    mine = select(Project.id).where(Project.org_id == organisation.id, Project.slug.in_(slugs))
    return or_(
        Agent.scope.in_([PLATFORM, ORG]), and_(Agent.scope == PROJECT, Agent.project_id.in_(mine)), own
    )


def permissions_of(principal: Principal, org: str, subject: Subject) -> tuple[AgentPermissions, str | None]:
    """What the DTO carries: the caller's rights on this agent and the sentence for the first they lack."""
    publish = decide(principal, org, subject, Action.PUBLISH)
    manage = decide(principal, org, subject, Action.MANAGE)
    release = decide(principal, org, subject, Action.RELEASE)
    pin = _can_pin_somewhere(principal, org, subject)
    forks = [
        scope
        for scope in CREATABLE
        if decide(principal, org, subject, Action.FORK, target_scope=scope).allowed
    ]
    reason = publish.reason or manage.reason
    if reason is None and not pin.allowed:
        reason = pin.reason
    return (
        AgentPermissions(
            publish=publish.allowed,
            manage=manage.allowed,
            pin=pin.allowed,
            release=release.allowed,
            fork_scopes=forks,
        ),
        reason,
    )


def _can_pin_somewhere(principal: Principal, org: str, subject: Subject) -> Decision:
    if subject.scope == PERSONAL:
        return _no(f"`{subject.slug}` is a personal agent: promote it or fork it into a project to pin it")
    if subject.scope == PROJECT:
        return decide(principal, org, subject, Action.PIN, project_slug=subject.project_slug)
    if _holds_anywhere(principal, org, Permission.PROJECT_WRITE):
        return ALLOWED
    return _no("pinning an agent needs the project_owner role on a project")


async def refusals_for_named_agents(
    session: AsyncSession, org_id: str, project_id: str, names: set[str]
) -> list[str]:
    """What a workflow of this project may not name: the personal agents, another project's agents.
    One sentence per agent, with its scope and the fix; empty when every name is fine or unknown (an
    unknown name is the validator's and the run's business)."""
    if not names:
        return []
    rows = (
        await session.execute(select(Agent).where(Agent.org_id == org_id, Agent.slug.in_(sorted(names))))
    ).scalars()
    refusals = []
    for agent in sorted(rows, key=lambda a: a.slug):
        refus = usable_in_project(agent.scope, agent.project_id, agent.slug, project_id)
        if refus is not None:
            refusals.append(f"{refus} (scope: {agent.scope})")
    return refusals
