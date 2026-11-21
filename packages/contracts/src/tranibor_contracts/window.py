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
- in the release that removes an entry's window, the name must be gone from that place: the release
  that should remove it cannot ship with it;
- `ROLE_STORED_AS` must be empty in the release where C1's writers switch, one release after the
  readers;
- the list is empty on 1.0, so the frozen surfaces carry no former name (ADR 0168).

The vocabulary guard (`tests/architecture/test_contracts_speak_no_trade.py`) does not read this file:
it names each former name once, on purpose, and its own test removes them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from .enums import ConnectorKind, FindingType, InboundEventType, MemoryKind, Role

__all__ = [
    "C1",
    "C2",
    "DELIVERY_CONNECTOR_KINDS",
    "DELIVERY_EVENT_TYPES",
    "DELIVERY_FINDING_TYPES",
    "DELIVERY_MEMORY_KINDS",
    "ROLE_STORED_AS",
    "ROLE_SUCCESSORS",
    "SOFTWARE_DELIVERY",
    "WINDOW",
    "D",
    "FormerName",
    "Window",
    "opening_release",
    "release",
    "removal_due",
    "writers_due",
]


#: The least a consumer (the enterprise edition, a plugin, a deployment's values) is given to switch: the
#: removal ships at least two weeks after the release that opened the window (ADR 0181, 2026-11-19).
GRACE = timedelta(days=14)


@dataclass(frozen=True, slots=True)
class Window:
    """One round's window, keyed on the releases actually cut, not on version numbers guessed ahead.

    Both names are served from the first release cut after the day the round merged (`opened_on`). The writers
    switch in the first release cut on or after `writers_switch_on`; the former names leave in the first
    release cut on or after `removed_on`, provided the release that opened the window is two weeks old
    by then. Whether a release is a patch or a minor does not matter. The releases and their dates are
    the release ledger (`tools/releases.json`), which `tools/bump_version.py` writes when it sets a
    version: the window test reads it, so it changes its verdict only in a release's own pull request,
    never on a calendar day."""

    round: str
    #: The day the round merged: the first release cut after that day serves both names.
    opened_on: date
    #: From the first release cut on or after this day, the writers of a stored value write the
    #: neutral spelling (ADR 0098 §4).
    writers_switch_on: date
    #: The first release cut on or after this day no longer has the former names. Its pull request
    #: removes them, rewrites the stored rows that still hold them, and takes their entries off this list.
    removed_on: date


def release(name: str) -> tuple[int, ...]:
    """`0.30.0` -> (0, 30, 0); a pre-release suffix (`0.30.0rc1`) counts as its release."""
    import re

    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", name)
    if match is None:
        raise ValueError(f"`{name}` is not a release number")
    return tuple(int(part) for part in match.groups())


def _cut(releases: Mapping[str, date], version: str) -> list[tuple[str, date]]:
    """The releases of the ledger up to `version`, in order."""
    return sorted(
        ((name, day) for name, day in releases.items() if release(name) <= release(version)),
        key=lambda item: release(item[0]),
    )


def opening_release(window: Window, releases: Mapping[str, date], version: str) -> tuple[str, date] | None:
    """The first release, up to `version`, cut after the day the round merged; `None` before it. A release
    of that same day may have been cut before the merge: the window then opens one release later, which
    only makes it longer."""
    return next(((name, day) for name, day in _cut(releases, version) if day > window.opened_on), None)


def _due(window: Window, releases: Mapping[str, date], version: str, on: date, grace: timedelta) -> bool:
    opened = opening_release(window, releases, version)
    if opened is None:
        return False
    return any(
        release(name) > release(opened[0]) and day >= on and day >= opened[1] + grace
        for name, day in _cut(releases, version)
    )


def writers_due(window: Window, releases: Mapping[str, date], version: str) -> bool:
    """Whether a release up to `version` (one after the opening) is the writers' switch, or after it."""
    return _due(window, releases, version, window.writers_switch_on, timedelta(0))


def removal_due(window: Window, releases: Mapping[str, date], version: str) -> bool:
    """Whether a release up to `version` is the one that removes the window's former names, or after it."""
    return _due(window, releases, version, window.removed_on, GRACE)


#: Round C1 of ADR 0181: neutral names for concepts the core owns. Merged on 2026-11-19, so served from
#: 0.27.17 (2026-11-20).
C1 = Window(
    round="C1",
    opened_on=date(2026, 11, 19),
    writers_switch_on=date(2026, 11, 30),
    removed_on=date(2026, 12, 7),
)

#: Round C2 of ADR 0181: what is the `software-delivery` plugin's leaves the core under the same strings.
#: The core keeps the plugin's values of its open lists for one window (`OpenList.kept`): their
#: enumeration members leave the contracts, and the values the core's list, in the release that removes
#: the window; the plugin's extension declares them from the release after 2026-11-21.
C2 = Window(
    round="C2",
    opened_on=date(2026, 11, 21),
    writers_switch_on=date(2026, 12, 7),
    removed_on=date(2026, 12, 14),
)

#: The first-party plugin whose values the core's open lists keep for the window of round C2.
SOFTWARE_DELIVERY = "software-delivery"
#: The inbound event types of a source control, a pipeline and a deployment (`scm.*`, `ci.*`, `cd.*`):
#: the plugin's since round C2, under the same strings (they are stored in `received_events`).
DELIVERY_EVENT_TYPES: frozenset[str] = frozenset(
    member.value for member in InboundEventType if member.value.split(".", 1)[0] in {"scm", "ci", "cd"}
)
#: The finding types and memory kinds of software delivery (ADR 0181 inventory, class (b)).
DELIVERY_FINDING_TYPES: frozenset[str] = frozenset(
    {FindingType.BUG, FindingType.PERF, FindingType.TECH_DEBT, FindingType.FLAKY_TEST}
)
DELIVERY_MEMORY_KINDS: frozenset[str] = frozenset({MemoryKind.FLAKY_TEST, MemoryKind.HOTSPOT})
#: The connector kinds of software delivery: a source control, a pipeline, a deployment.
DELIVERY_CONNECTOR_KINDS: frozenset[str] = frozenset({ConnectorKind.SCM, ConnectorKind.CI, ConnectorKind.CD})

#: Round D of ADR 0181: what leaves the core with the code of software delivery (its connectors, the
#: release train), not before that code moves (S83-05, 2027-04-19 → 2027-05-28). The connector kinds
#: `scm`, `ci` and `cd` wait for it: the core's adapters still register the `github`, `gitlab`, `tekton`
#: and `argocd` types under them. The list is empty by 2027-06-11 (ADR 0181, 2026-11-19, point 6).
D = Window(
    round="D",
    opened_on=date(2026, 11, 21),
    writers_switch_on=date(2027, 4, 19),
    removed_on=date(2027, 5, 28),
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


def _kept(
    enum: type[InboundEventType | FindingType | MemoryKind | ConnectorKind],
    values: frozenset[str],
    window: Window | None = None,
) -> tuple[FormerName, ...]:
    """The members of a contract's enumeration whose value the `software-delivery` plugin declares."""
    return tuple(
        FormerName(
            f"{enum.__name__}.{member.name}",
            f"`{member.value}`, declared by the software-delivery plugin",
            "python",
            f"py:tranibor_contracts.enums:{enum.__name__}.{member.name}",
            window or C2,
        )
        for member in enum
        if member.value in values
    )


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
    # Round C2: the open lists' values of software delivery (ADR 0181 inventory, class (b)).
    *_kept(InboundEventType, DELIVERY_EVENT_TYPES),
    *_kept(FindingType, DELIVERY_FINDING_TYPES),
    *_kept(MemoryKind, DELIVERY_MEMORY_KINDS),
    # Round D: the connector kinds leave with the connector types the core still registers under them.
    *_kept(ConnectorKind, DELIVERY_CONNECTOR_KINDS, D),
)

#: A former role and its successor: `Role.neutral` reads through it.
ROLE_SUCCESSORS: dict[Role, Role] = {Role.DEVELOPER: Role.CONTRIBUTOR, Role.RELEASE_CAPTAIN: Role.APPROVER}
#: The spelling a row is written with until C1's writers switch: the former one, which every reader of
#: the previous release knows. Emptied in that release (`Role.stored` is then itself).
ROLE_STORED_AS: dict[Role, Role] = {new: old for old, new in ROLE_SUCCESSORS.items()}
