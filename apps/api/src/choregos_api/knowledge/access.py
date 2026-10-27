# SPDX-License-Identifier: Apache-2.0
"""Who may read a knowledge base, who may change it, and which runs are served it (ADR 0051, 0081).

Two separate questions, on purpose:

- **Reach** (`scope`: `org`, `projects`, `groups`; plus `restricted`) says which humans read the base
  and which runs are served it. Row-level security keeps a base inside its organisation; inside it:
  `org`: every member reads it, every project's runs are served it; `projects`: members who can read
  one of the listed projects, runs of those projects only; `groups`: humans of one of the listed IdP
  groups, and a run carries no group, so it is never served a group-scoped base. `restricted`
  withdraws the human reading: people read it only through a grant, while runs are still served.
- **Roles** (`viewer`, `editor`, `owner`) say what a person may do. They come from a grant to a user, a
  group or a project, or are implicit: an organisation administrator (`agent:manage`) is owner of every
  base, and the owners of the project that owns the base (`owner_project`, S39-14) are its owners. A
  grant gives people a role; it never serves a run.

One function, `decide`, answers "may this caller do this here" with the sentence that says why not
(the same pattern as ADR 0061 and 0074); the console shows what it says.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..rbac import Permission, Principal
from ..services.human_groups import groups_for_org

VIEWER, EDITOR, OWNER = "viewer", "editor", "owner"
ROLES = (VIEWER, EDITOR, OWNER)
_RANK = {VIEWER: 1, EDITOR: 2, OWNER: 3}


class KnowledgeAction(StrEnum):
    READ = "read"
    #: upload, delete, convert, sources, sync, eval, reindex, rename, serving mode
    EDIT = "edit"
    #: spends model budget (D11-1: editors may)
    DISTILL = "distill"
    #: change the grants
    SHARE = "share"
    ARCHIVE = "archive"
    #: change scope, projects, groups or `restricted` (D11-2: organisation administrators only)
    WIDEN = "widen"


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str | None = None


_ALLOWED = Decision(True)


def can_manage(principal: Principal, org: str) -> bool:
    return principal.can(Permission.AGENT_MANAGE, org)


def can_curate(principal: Principal, org: str, base: Any) -> bool:
    """Whether this caller is an implicit owner: an organisation administrator, or the owner of the
    project that owns the base (S39-14). Grants are not looked at here; see `decide`."""
    if can_manage(principal, org):
        return True
    owner = getattr(base, "owner_project", None)
    return bool(owner) and principal.can(Permission.PROJECT_WRITE, org, owner)


def _member_of(principal: Principal, org: str) -> bool:
    return org in principal.org_roles or any(key.startswith(f"{org}/") for key in principal.project_roles)


def _reaches(principal: Principal, org: str, base: Any) -> bool:
    """Reading through the base's reach (scope), which `restricted` withdraws."""
    if getattr(base, "restricted", False):
        return False
    if base.scope == "org":
        return principal.can(Permission.PROJECT_READ, org)
    if base.scope == "projects":
        return any(principal.can(Permission.PROJECT_READ, org, project) for project in base.projects or [])
    if base.scope == "groups":
        return principal.can(Permission.PROJECT_READ, org) and bool(
            groups_for_org(principal.groups, org) & set(base.groups or [])
        )
    return False


def _holds(principal: Principal, org: str, grant: Any) -> bool:
    kind, subject = grant.subject_type, str(grant.subject)
    if kind == "user":
        return subject == principal.user_id or subject.lower() == principal.email.lower()
    if kind == "group":
        return subject in groups_for_org(principal.groups, org)
    if kind == "project":
        # A member of the project, not an organisation-wide role that merely reads every project.
        return f"{org}/{subject}" in principal.project_roles
    return False


def role_of(principal: Principal, org: str, base: Any, grants: Iterable[Any] = ()) -> str | None:
    """The best role the caller holds on this base, or None for a plain reader through the reach.

    Nobody outside the organisation holds a role: a grant never crosses organisations.
    """
    if can_curate(principal, org, base):
        return OWNER
    if not _member_of(principal, org):
        return None
    held = [grant.role for grant in grants if _holds(principal, org, grant) and grant.role in _RANK]
    return max(held, key=_RANK.__getitem__, default=None)


def visible_to(principal: Principal, org: str, base: Any, grants: Iterable[Any] = ()) -> bool:
    """Whether a human (or a token acting for one) may read this base and its documents."""
    if can_manage(principal, org):
        return True
    return _reaches(principal, org, base) or role_of(principal, org, base, grants) is not None


_NEEDS = {
    KnowledgeAction.EDIT: EDITOR,
    KnowledgeAction.DISTILL: EDITOR,
    KnowledgeAction.SHARE: OWNER,
    KnowledgeAction.ARCHIVE: OWNER,
}
_REASONS = {
    KnowledgeAction.EDIT: "changing this knowledge base needs the editor role on it, or org_admin, "
    "or project_owner of its project",
    KnowledgeAction.DISTILL: "distilling this knowledge base needs the editor role on it, or org_admin, "
    "or project_owner of its project",
    KnowledgeAction.SHARE: "sharing this knowledge base needs the owner role on it, or org_admin, "
    "or project_owner of its project",
    KnowledgeAction.ARCHIVE: "archiving this knowledge base needs the owner role on it, or org_admin, "
    "or project_owner of its project",
    KnowledgeAction.WIDEN: "changing who reads this knowledge base or whose runs are served it "
    "needs the org_admin role",
    KnowledgeAction.READ: "this knowledge base is not visible to you",
}


def decide(
    principal: Principal, org: str, base: Any, action: KnowledgeAction, grants: Iterable[Any] = ()
) -> Decision:
    """The answer to "may this person do this here", with the sentence that says why not."""
    grants = list(grants)
    if action is KnowledgeAction.READ:
        allowed = visible_to(principal, org, base, grants)
    elif action is KnowledgeAction.WIDEN:
        allowed = can_manage(principal, org)
    else:
        role = role_of(principal, org, base, grants)
        allowed = role is not None and _RANK[role] >= _RANK[_NEEDS[action]]
    return _ALLOWED if allowed else Decision(False, _REASONS[action])


def served_to_project(base: Any, project_slug: str) -> bool:
    """Whether a run of this project may be served this base (and the base is usable at all).

    Neither a grant nor `restricted` is looked at: they decide what people do, not what runs read.
    """
    if base.status != "active":
        return False
    if base.scope == "org":
        return True
    if base.scope == "projects":
        return project_slug in (base.projects or [])
    return False
