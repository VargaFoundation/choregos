# SPDX-License-Identifier: Apache-2.0
"""The window list: every former name a round keeps beside its successor, and the release that removes it.

ADR 0181 renames the core's own concepts in rounds, before the 1.0 freeze. A round adds the neutral
name beside the former one: a response carries both, a request accepts either, a reader of a stored
value takes both spellings (ADR 0098 §4). The former name then stays for one window and leaves in its
own pull request, at least two weeks after the round's release (ADR 0181, Decision (2026-11-19) point
6). There is no alias policy any more (ADR 0098): this list is the only place a former name may be
kept, and `tests/architecture/test_the_window_list.py` holds it both ways:

- every entry names a place where its former name is today, so an entry whose name already left is
  refused, and the list shrinks with the names;
- once the repository's version reaches an entry's `removed_in`, the name must be gone from that place:
  the release that should remove it cannot ship with it;
- `ROLE_STORED_AS` must be empty from the window's `writers_switch_in`: the writers switch one release
  after the readers;
- the list is empty on 1.0, so the frozen surfaces carry no former name (ADR 0168).

The vocabulary guard (`tests/architecture/test_contracts_speak_no_trade.py`) does not read this file:
it names each former name once, on purpose, and its own test removes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from .enums import Role

__all__ = [
    "C1",
    "ROLE_STORED_AS",
    "ROLE_SUCCESSORS",
    "WINDOW",
    "FormerName",
    "Window",
]


@dataclass(frozen=True, slots=True)
class Window:
    """One round's window: both names are served from `opened_in`, the former ones leave in `removed_in`."""

    round: str
    #: The first release that serves both names.
    opened_in: str
    #: From this release the writers of a stored value write the neutral spelling (ADR 0098 §4).
    writers_switch_in: str
    #: The release that no longer has the former names. Its pull request removes them, rewrites the
    #: stored rows that still hold them, and takes their entries off this list.
    removed_in: str
    #: The removal ships no earlier than this: two weeks after the round's release, the least a
    #: consumer (the enterprise edition, a plugin, a deployment's values) is given to switch.
    not_before: date


#: Round C1 of ADR 0181: neutral names for concepts the core owns. 0.28.0 is the first minor after
#: 2026-11-19; at a minor a week, 0.30.0 comes two weeks later.
C1 = Window(
    round="C1",
    opened_in="0.28.0",
    writers_switch_in="0.29.0",
    removed_in="0.30.0",
    not_before=date(2026, 12, 7),
)

Surface = Literal["python", "rest", "dsl", "stage-result", "sign-in"]


@dataclass(frozen=True, slots=True)
class FormerName:
    """A former name, where it is today, and what replaces it.

    `where` is a locator the window test resolves:

    - `py:<module>:<Attr>.<attr>`, an attribute; `[<key>]` at the end asks whether a mapping has that key;
    - `literal:<module>:<Model>.<field>`, the values of a `Literal` field (with `value`);
    - `openapi:<json pointer>` in `openapi.yaml`, `schema:<file>#<json pointer>` in `schemas/`; with
      `value`, the name is that value of the list the pointer reaches (an `enum`).
    """

    name: str
    successor: str
    surface: Surface
    where: str
    window: Window
    value: str | None = None


_ROLE = "#/components/schemas/Role/enum"
_APPROVER = "#/components/schemas/ActionApproval/properties/approvers/items/properties/role/enum"
_WORKFLOW_APPROVER = (
    "workflow.schema.json#/$defs/actionApproval/properties/approvers/items/properties/role/enum"
)


def _rest(schema: str, name: str, successor: str) -> FormerName:
    return FormerName(name, successor, "rest", f"openapi:/components/schemas/{schema}/properties/{name}", C1)


#: Every former name kept for a window, with its successor and its window.
WINDOW: tuple[FormerName, ...] = (
    # Roles (ADR 0181 inventory, class (c)). A row of `memberships.role` keeps its spelling until the
    # removal release rewrites it; `Role.neutral` reads both as one role meanwhile.
    FormerName(
        "Role.DEVELOPER", "Role.CONTRIBUTOR", "python", "py:tranibor_contracts.enums:Role.DEVELOPER", C1
    ),
    FormerName(
        "Role.RELEASE_CAPTAIN",
        "Role.APPROVER",
        "python",
        "py:tranibor_contracts.enums:Role.RELEASE_CAPTAIN",
        C1,
    ),
    FormerName("developer", "contributor", "rest", f"openapi:{_ROLE[1:]}", C1, value="developer"),
    FormerName("release_captain", "approver", "rest", f"openapi:{_ROLE[1:]}", C1, value="release_captain"),
    FormerName("developer", "contributor", "rest", f"openapi:{_APPROVER[1:]}", C1, value="developer"),
    FormerName(
        "release_captain", "approver", "rest", f"openapi:{_APPROVER[1:]}", C1, value="release_captain"
    ),
    FormerName("developer", "contributor", "dsl", f"schema:{_WORKFLOW_APPROVER}", C1, value="developer"),
    FormerName(
        "release_captain", "approver", "dsl", f"schema:{_WORKFLOW_APPROVER}", C1, value="release_captain"
    ),
    FormerName(
        "developer",
        "contributor",
        "dsl",
        "literal:tranibor_contracts.workflow:ActionApprover.role",
        C1,
        "developer",
    ),
    FormerName(
        "release_captain",
        "approver",
        "dsl",
        "literal:tranibor_contracts.workflow:ActionApprover.role",
        C1,
        "release_captain",
    ),
    # The role groups an identity provider sends (`choregos:<org>:<group>`, docs/site/host/identity.md):
    # a deployment renames its groups inside the window.
    FormerName(
        "developers", "contributors", "sign-in", "py:tranibor_api.routers.auth:GROUP_ROLES[developers]", C1
    ),
    FormerName(
        "release-captains",
        "approvers",
        "sign-in",
        "py:tranibor_api.routers.auth:GROUP_ROLES[release-captains]",
        C1,
    ),
    # The REST components' fields (class (c)): responses carry both names during the window.
    _rest("WorkItem", "pr_url", "links[rel=change_request]"),
    _rest("WorkItem", "repository", "workspace"),
    _rest("WorkItem", "repository_fixed", "workspace_fixed"),
    _rest("ProcessStep", "writes_branch", "writes_workspace"),
    _rest("WorkflowCatalogueEntry", "needs_repository", "needs_workspace"),
    _rest("ArtifactFlow", "in_pull_request", "in_change_request"),
    _rest("ProjectStats", "first_pass_merge_rate", "first_pass_acceptance_rate"),
    _rest("MemoryAbGroup", "first_pass_merge_rate", "first_pass_acceptance_rate"),
    # A step's result: `pr_url` becomes a link; a reader takes either (`Artifacts.link`).
    FormerName(
        "pr_url",
        "links[rel=change_request]",
        "stage-result",
        "schema:stage-result.schema.json#/properties/artifacts/properties/pr_url",
        C1,
    ),
    FormerName(
        "Artifacts.pr_url",
        "Artifacts.links",
        "python",
        "py:tranibor_contracts.stage:Artifacts.model_fields[pr_url]",
        C1,
    ),
)

#: A former role and its successor: `Role.neutral` reads through it.
ROLE_SUCCESSORS: dict[Role, Role] = {Role.DEVELOPER: Role.CONTRIBUTOR, Role.RELEASE_CAPTAIN: Role.APPROVER}
#: The spelling a row is written with until the window's `writers_switch_in`: the former one, which
#: every reader of the previous release knows. Emptied in that release (`Role.stored` is then itself).
ROLE_STORED_AS: dict[Role, Role] = {new: old for old, new in ROLE_SUCCESSORS.items()}
