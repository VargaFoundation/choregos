"""The window list holds every former name a round keeps, and each leaves at its release (ADR 0181 C1).

`tranibor_contracts.window.WINDOW` is the only place a former name may stay beside its successor
(ADR 0098 left no alias policy). This test holds it both ways: an entry names a place where its former
name is today (an entry whose name already left is stale and is refused), and once the repository's
version reaches the entry's `removed_in` the name must be gone from that place. So the release that
should remove a name cannot ship with it, and the list is empty on 1.0.

To see it fail, set `removed_in` of `C1` to the current version: every entry is reported, naming its
place. Or delete `pr_url` from the `WorkItem` of `openapi.yaml` without taking its entry off the list.
"""

from __future__ import annotations

import importlib
import json
import re
import tomllib
import typing
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from tranibor_contracts import Role
from tranibor_contracts.window import C1, ROLE_STORED_AS, ROLE_SUCCESSORS, WINDOW, FormerName, Window

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages/contracts"


def version() -> tuple[int, ...]:
    text = (CONTRACTS / "pyproject.toml").read_text(encoding="utf-8")
    return release(tomllib.loads(text)["project"]["version"])


def release(name: str) -> tuple[int, ...]:
    """`0.30.0` -> (0, 30, 0); a pre-release suffix (`0.30.0rc1`) counts as its release."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", name)
    assert match, f"`{name}` is not a release number"
    return tuple(int(part) for part in match.groups())


def _pointer(document: Any, pointer: str) -> Any:
    node = document
    for raw in pointer.lstrip("/").split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(key)]
        elif isinstance(node, dict) and key in node:
            node = node[key]
        else:
            raise LookupError(key)
    return node


def _attribute(target: str) -> Any:
    module, _, path = target.partition(":")
    node: Any = importlib.import_module(module)
    for part in path.split("."):
        node = getattr(node, part)
    return node


def present(entry: FormerName) -> bool:
    """Whether the former name is still where the entry says."""
    kind, _, target = entry.where.partition(":")
    try:
        if kind == "py":
            key = re.search(r"\[([^\]]+)\]$", target)
            if key:
                return key.group(1) in _attribute(target[: key.start()])
            _attribute(target)
            return True
        if kind == "literal":
            module, _, path = target.partition(":")
            model, _, field = path.partition(".")
            annotation = getattr(importlib.import_module(module), model).model_fields[field].annotation
            return entry.value in typing.get_args(annotation)
        if kind == "openapi":
            document = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))
            found = _pointer(document, target)
        elif kind == "schema":
            file, _, pointer = target.partition("#")
            document = json.loads((CONTRACTS / "schemas" / file).read_text(encoding="utf-8"))
            found = _pointer(document, pointer)
        else:
            raise AssertionError(f"unknown locator `{entry.where}`")
    except (LookupError, AttributeError, ImportError, IndexError, ValueError):
        return False
    return entry.value in found if entry.value is not None else True


def _label(entry: FormerName) -> str:
    named = f"`{entry.value}` in " if entry.value else ""
    return f"{entry.name} -> {entry.successor} ({entry.surface}, {named}{entry.where})"


@pytest.mark.parametrize("entry", WINDOW, ids=_label)
def test_a_former_name_is_where_the_list_says_until_its_release_removes_it(entry: FormerName) -> None:
    if version() >= release(entry.window.removed_in):
        assert not present(entry), (
            f"{_label(entry)} is still there in {'.'.join(map(str, version()))}, the release that removes "
            f"it ({entry.window.round} of ADR 0181): remove it, rewrite the rows that hold it, and take its "
            "entry off `tranibor_contracts.window.WINDOW`"
        )
    else:
        assert present(entry), (
            f"{_label(entry)} is not there any more: take its entry off `tranibor_contracts.window.WINDOW` "
            "in the same pull request"
        )


def test_the_writers_switch_one_release_after_the_readers() -> None:
    windows = {entry.window for entry in WINDOW} | {C1}
    switched = [w for w in windows if version() >= release(w.writers_switch_in)]
    if any(w is C1 for w in switched):
        assert not ROLE_STORED_AS, (
            f"{C1.writers_switch_in} writes the neutral role names (ADR 0098 §4): empty `ROLE_STORED_AS`"
        )
    else:
        assert {old.stored for old in ROLE_SUCCESSORS} == set(ROLE_SUCCESSORS)
        assert {new.stored for new in ROLE_SUCCESSORS.values()} == set(ROLE_SUCCESSORS)


@pytest.mark.parametrize("window", sorted({entry.window for entry in WINDOW} | {C1}, key=lambda w: w.round))
def test_a_window_is_ordered_and_its_removal_waits_two_weeks(window: Window) -> None:
    assert release(window.opened_in) < release(window.writers_switch_in) < release(window.removed_in)
    if version() >= release(window.removed_in):
        assert date.today() >= window.not_before, (
            f"{window.removed_in} removes the former names of {window.round} before {window.not_before}, "
            "two weeks after the round's release (ADR 0181): move `removed_in` to a later release"
        )


def test_the_list_is_empty_on_1_0() -> None:
    if version() >= (1, 0, 0):
        assert not WINDOW, "1.0 freezes the surfaces (ADR 0168): no former name may be kept on them"


def test_a_former_role_and_its_successor_are_one_role() -> None:
    assert Role.DEVELOPER.neutral is Role.CONTRIBUTOR and Role.CONTRIBUTOR.neutral is Role.CONTRIBUTOR
    assert Role.RELEASE_CAPTAIN.neutral is Role.APPROVER and Role.APPROVER.neutral is Role.APPROVER
    assert Role.ORG_ADMIN.neutral is Role.ORG_ADMIN and Role.ORG_ADMIN.stored is Role.ORG_ADMIN
    assert set(Role.current()) == {role.neutral for role in Role}
    assert not set(Role.current()) & set(ROLE_SUCCESSORS)


def test_the_locator_finds_a_name_and_misses_one_that_left() -> None:
    there = FormerName(
        "pr_url", "links", "rest", "openapi:/components/schemas/WorkItem/properties/pr_url", C1
    )
    gone = FormerName(
        "pr_number", "links", "rest", "openapi:/components/schemas/WorkItem/properties/pr_number", C1
    )
    value = FormerName(
        "developer", "contributor", "rest", "openapi:/components/schemas/Role/enum", C1, "developer"
    )
    missing = FormerName(
        "captain", "approver", "rest", "openapi:/components/schemas/Role/enum", C1, "captain"
    )
    module = FormerName("X", "Y", "python", "py:tranibor_contracts.enums:Role.CAPTAIN", C1)
    assert present(there) and present(value)
    assert not present(gone) and not present(missing) and not present(module)
