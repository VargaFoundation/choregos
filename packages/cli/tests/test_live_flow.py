# SPDX-License-Identifier: Apache-2.0
"""S67-26: the live flow's reduction, on the fixtures it shares with the web (ADR 0187 §4).

`apps/web/src/lib/live-flow.ts` is the same function in TypeScript. Both suites run EVERY file of
`packages/contracts/fixtures/live/`: they list the directory, they do not name the fixtures, so a
fixture added is run by both. `test_the_web_suite_reads_the_same_directory` keeps the web suite honest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tranibor_cli import live_flow

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "packages" / "contracts" / "fixtures" / "live"
WEB_TEST = ROOT / "apps" / "web" / "tests" / "live-flow.test.ts"
FILES = sorted(FIXTURES.glob("*.json"))


def test_there_are_fixtures() -> None:
    assert len(FILES) >= 7
    assert [json.loads(f.read_text())["name"] + ".json" for f in FILES] == [f.name for f in FILES]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_the_reduction_gives_the_fixtures_expected_view(path: Path) -> None:
    fixture = json.loads(path.read_text())
    got = live_flow.reduce(
        fixture["graph"], fixture["items"], fixture["steps"], fixture["now"], fixture["options"]
    )
    assert got == fixture["expected"]


def test_the_web_suite_reads_the_same_directory() -> None:
    """The TypeScript suite lists `fixtures/live` itself, so a fixture added is run there as here."""
    source = WEB_TEST.read_text()
    assert "packages/contracts/fixtures/live" in source
    assert "readdirSync(DIR)" in source
    assert not [f.name for f in FILES if f.name in source], "the web suite must not name a fixture"


def _item(state: str = "a", **extra: Any) -> dict[str, Any]:
    return {"id": "w1", "tracker_key": "K-1", "title": "T", "state": state, "totals": {}, **extra}


def _entry(**extra: Any) -> dict[str, Any]:
    return {
        "kind": "run",
        "id": "r1",
        "status": "running",
        "work_item_id": "w1",
        "stage_role": "dev",
        "started_at": "2026-11-18T10:00:00Z",
        "can_stop": True,
        **extra,
    }


GRAPH = {
    "nodes": [{"id": "a", "display": "A", "kind": "agent"}, {"id": "b", "display": "B", "kind": "agent"}],
    "edges": [],
}


def test_elapsed_follows_the_given_clock_never_below_zero() -> None:
    state = live_flow.apply_event(live_flow.initial_state(GRAPH, [_item()]), "activity.upsert", _entry())
    assert live_flow.view(state, "2026-11-18T10:01:05Z")["cards"][0]["running"]["elapsed_ms"] == 65_000
    assert live_flow.view(state, "2026-11-18T09:00:00Z")["cards"][0]["running"]["elapsed_ms"] == 0


def test_the_reduction_does_not_mutate_the_state_it_is_given() -> None:
    first = live_flow.initial_state(GRAPH, [_item()])
    frozen = json.dumps([first["slots"], first["ticker"]], sort_keys=True)
    live_flow.apply_event(first, "activity.upsert", _entry())
    live_flow.apply_items(first, [_item("b")], "2026-11-18T10:00:00Z")
    assert json.dumps([first["slots"], first["ticker"]], sort_keys=True) == frozen


def test_cut_makes_one_line() -> None:
    assert live_flow.cut("a\n  b", 10) == "a b"
    assert live_flow.cut("abcdef", 4) == "abc…"


def test_the_module_imports_no_renderer() -> None:
    source = (ROOT / "packages" / "cli" / "src" / "tranibor_cli" / "live_flow.py").read_text()
    assert "textual" not in source.lower().replace("no textual", "")
    assert "import rich" not in source and "from rich" not in source
