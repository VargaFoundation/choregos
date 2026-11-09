# SPDX-License-Identifier: Apache-2.0
"""A human actor's group may name the project it runs in (S59-01).

`group: "{project}-maintainers"` makes ONE workflow serve every project of an organisation, each step
answered by that project's people: the identity provider already sends per-project groups
(`iris-maintainers`, `atlas-maintainers`), and a workflow shared to the organisation's catalogue
(ADR 0111) can now address them without a copy per project. `{project}` is the project's slug, read
where a person answers or is looked for; no other placeholder exists, and a train's approval group
(read by the release train of a project, ADR 0041) does not take one.
"""

from __future__ import annotations

import re

#: The only placeholder a group may carry.
PROJECT = "{project}"

_BRACES = re.compile(r"[{}]")


def group_for(group: str, project_slug: str | None) -> str:
    """The group a person must carry in `project_slug`. Without a project (an organisation-level
    listing), the group is returned as written."""
    name = group.strip()
    return name.replace(PROJECT, project_slug) if project_slug else name


def is_per_project(group: str) -> bool:
    return PROJECT in group


def placeholder_error(group: str) -> str | None:
    """Why a group's braces are refused, or `None`: only `{project}` is a placeholder."""
    rest = group.replace(PROJECT, "")
    if _BRACES.search(rest):
        return f"`{group}`: a group names only the `{PROJECT}` placeholder"
    return None


def matches_some(group: str, known: set[str]) -> bool:
    """Whether some group of `known` is `group` for some project: what an organisation-level check can
    say of `{project}-maintainers` (some project's maintainers exist)."""
    if not is_per_project(group):
        return group.strip() in known
    pattern = re.compile(
        "^" + re.escape(group.strip()).replace(re.escape(PROJECT), r"[a-z0-9][a-z0-9-]*") + "$"
    )
    return any(pattern.match(name) for name in known)


__all__ = ["PROJECT", "group_for", "is_per_project", "matches_some", "placeholder_error"]
