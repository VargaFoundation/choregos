"""The window list holds every former name a round keeps, and each leaves at its release (ADR 0181 C1).

`tranibor_contracts.window.WINDOW` is the only place a former name may stay beside its successor
(ADR 0098 left no alias policy). This test holds it both ways: an entry names a place where its former
name is today (an entry whose name already left is stale and is refused), and once the repository's
release that removes the entry's window is cut, the name must be gone from that place. So the release
that should remove a name cannot ship with it, and the list is empty on 1.0.

A window is keyed on the releases actually cut (S83-04w): the release ledger `tools/releases.json`, which
`tools/bump_version.py` writes in a release's own pull request. A window closes in the first release cut
on or after its `removed_on`, two weeks at least after the release that opened it, whether that release
is a patch (0.27.18) or a minor. The verdict moves only when the version and the ledger move, never on a
calendar day.

To see it fail, set `removed_on` of `C1` to a day before the last release of the ledger and that
release two weeks after 0.27.17: every entry is reported, naming its place. Or delete `pr_url` from
the `WorkItem` of `openapi.yaml` without taking its entry off the list.
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
from tranibor_contracts.window import (
    C1,
    GRACE,
    ROLE_STORED_AS,
    ROLE_SUCCESSORS,
    WINDOW,
    FormerName,
    Window,
    opening_release,
    release,
    removal_due,
    writers_due,
)

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages/contracts"


LEDGER = ROOT / "tools/releases.json"


def current() -> str:
    text = (CONTRACTS / "pyproject.toml").read_text(encoding="utf-8")
    return str(tomllib.loads(text)["project"]["version"])


def version() -> tuple[int, ...]:
    return release(current())


def releases() -> dict[str, date]:
    document = json.loads(LEDGER.read_text(encoding="utf-8"))
    return {name: date.fromisoformat(day) for name, day in document["releases"].items()}


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
    if removal_due(entry.window, releases(), current()):
        assert not present(entry), (
            f"{_label(entry)} is still there in {current()}, the release that removes it "
            f"({entry.window.round} of ADR 0181): remove it, rewrite the rows that hold it, and take its "
            "entry off `tranibor_contracts.window.WINDOW`"
        )
    else:
        assert present(entry), (
            f"{_label(entry)} is not there any more: take its entry off `tranibor_contracts.window.WINDOW` "
            "in the same pull request"
        )


def test_the_writers_switch_one_release_after_the_readers() -> None:
    if writers_due(C1, releases(), current()):
        assert not ROLE_STORED_AS, (
            f"{current()} writes the neutral role names (ADR 0098 §4): empty `ROLE_STORED_AS`"
        )
    else:
        assert {old.stored for old in ROLE_SUCCESSORS} == set(ROLE_SUCCESSORS)
        assert {new.stored for new in ROLE_SUCCESSORS.values()} == set(ROLE_SUCCESSORS)


@pytest.mark.parametrize("window", sorted({entry.window for entry in WINDOW} | {C1}, key=lambda w: w.round))
def test_a_window_is_ordered_and_its_removal_waits_two_weeks(window: Window) -> None:
    assert window.opened_on < window.writers_switch_on < window.removed_on
    assert window.removed_on >= window.opened_on + GRACE


def test_the_ledger_holds_the_current_release_in_order() -> None:
    """The release a commit carries is in the ledger (`tools/bump_version.py` writes both), and the days
    never go back as the versions go up."""
    ledger = releases()
    assert current() in ledger or "-" in current(), "run tools/bump_version.py: it writes the ledger"
    days = [day for _, day in sorted(ledger.items(), key=lambda item: release(item[0]))]
    assert days == sorted(days)
    # C1 merged on 2026-11-19 and was first served by 0.27.17, as STATUS says.
    assert opening_release(C1, ledger, "0.27.17") == ("0.27.17", date(2026, 11, 20))


def test_a_window_closes_in_the_first_release_cut_on_its_day_patch_or_minor() -> None:
    """What the ledger decides, on a window of 2026-11-21 → 2026-12-14."""
    window = Window("T", date(2026, 11, 21), date(2026, 12, 7), date(2026, 12, 14))
    ledger = {"0.27.17": date(2026, 11, 20), "0.27.18": date(2026, 11, 24), "0.27.19": date(2026, 12, 8)}
    assert opening_release(window, ledger, "0.27.17") is None, "the round is in no release yet"
    same_day = {"0.27.17": date(2026, 11, 21), "0.27.18": date(2026, 11, 22)}
    assert opening_release(window, same_day, "0.27.18") == ("0.27.18", date(2026, 11, 22)), "maybe cut before"
    assert opening_release(window, ledger, "0.27.19") == ("0.27.18", date(2026, 11, 24))
    assert writers_due(window, ledger, "0.27.19") and not removal_due(window, ledger, "0.27.19")
    # A patch on the removal day closes it; a minor would as well. Nothing is due before it is cut.
    ledger["0.27.20"] = date(2026, 12, 14)
    assert removal_due(window, ledger, "0.27.20") and not removal_due(window, ledger, "0.27.19")
    assert removal_due(window, {**ledger, "0.28.0": date(2026, 12, 15)}, "0.28.0")
    # Two weeks after the opening release at least: a late opening pushes the removal back.
    late = {"0.27.17": date(2026, 11, 20), "0.27.18": date(2026, 12, 10), "0.27.19": date(2026, 12, 15)}
    assert not removal_due(window, late, "0.27.19")
    assert removal_due(window, {**late, "0.27.20": date(2026, 12, 24)}, "0.27.20")
    # The release that opens a window never closes it, even cut after its day.
    assert not removal_due(window, {"0.27.17": date(2026, 11, 20), "0.27.18": date(2027, 1, 4)}, "0.27.18")


def test_the_release_writes_the_ledger_and_checks_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("bump_version", ROOT / "tools/bump_version.py")
    assert spec is not None and spec.loader is not None
    bump = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bump)
    ledger = tmp_path / "releases.json"
    ledger.write_text(json.dumps({"releases": {"0.27.17": "2026-11-20"}}), encoding="utf-8")
    monkeypatch.setattr(bump, "LEDGER", ledger)
    monkeypatch.setattr(bump, "ROOT", tmp_path)
    assert bump.record_release("0.27.18", check=True) == 1, "a release missing from the ledger is refused"
    assert bump.record_release("0.27.18", check=False, today=date(2026, 11, 24)) == 0
    assert json.loads(ledger.read_text(encoding="utf-8"))["releases"]["0.27.18"] == "2026-11-24"
    assert bump.record_release("0.27.18", check=True) == 0
    assert bump.record_release("0.28.0-rc1", check=True) == 0, "a pre-release is not a release"


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
