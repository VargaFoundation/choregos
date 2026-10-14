# SPDX-License-Identifier: Apache-2.0
"""Who may read a knowledge base, and which runs may be served one (ADR 0051).

Row-level security keeps a knowledge base inside its organisation; inside it, `scope` narrows:

- `org`: every member reads it, every project's runs are served it;
- `projects`: members who can read one of the listed projects; runs of those projects only;
- `groups`: humans of one of the listed IdP groups. A run carries no group, so it is never served
  a group-scoped base.

The organisation's administrators (`agent:manage`) see every base: they curate them.
"""

from __future__ import annotations

from typing import Any

from ..rbac import Permission, Principal


def can_manage(principal: Principal, org: str) -> bool:
    return principal.can(Permission.AGENT_MANAGE, org)


def visible_to(principal: Principal, org: str, base: Any) -> bool:
    """Whether a human (or a token acting for one) may read this base and its documents."""
    if can_manage(principal, org):
        return True
    if base.scope == "org":
        return principal.can(Permission.PROJECT_READ, org)
    if base.scope == "projects":
        return any(principal.can(Permission.PROJECT_READ, org, project) for project in base.projects or [])
    if base.scope == "groups":
        return principal.can(Permission.PROJECT_READ, org) and bool(
            set(principal.groups) & set(base.groups or [])
        )
    return False


def served_to_project(base: Any, project_slug: str) -> bool:
    """Whether a run of this project may be served this base (and the base is usable at all)."""
    if base.status != "active":
        return False
    if base.scope == "org":
        return True
    if base.scope == "projects":
        return project_slug in (base.projects or [])
    return False
