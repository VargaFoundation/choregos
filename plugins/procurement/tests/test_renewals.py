# SPDX-License-Identifier: Apache-2.0
"""`tranibor-procurement renewals`: the contracts that end soon, capped for a fan-out."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from tranibor_procurement.cli import main
from tranibor_procurement.relay import RelayError


def _contracts(count: int) -> list[dict[str, Any]]:
    day = dt.date(2026, 12, 1)
    # Listed in reverse, so that the order is the command's and not the server's.
    return [
        {"id": f"c-{n:03d}", "supplier": "Alderbrook", "end_date": (day + dt.timedelta(days=n)).isoformat()}
        for n in reversed(range(count))
    ]


def _job(root: Path) -> dict[str, Any]:
    done: dict[str, Any] = json.loads((root / ".choregos" / "job.json").read_text(encoding="utf-8"))
    return done


def test_sixty_expiring_contracts_give_fifty_children_and_ten_left_out(workspace: Path) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def relay(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        seen.append((operation, arguments))
        return {"contracts": _contracts(60)}

    assert main(["renewals", "--within-days", "90", "--max", "50"], relay=relay) == 0
    done = _job(workspace)
    contracts = done["outputs"]["contracts"]
    assert done["facts"] == {"contracts_expiring": 50, "contracts_left_out": 10}
    assert contracts["expiring"] == [f"c-{n:03d}" for n in range(50)]
    assert contracts["left_out"] == [f"c-{n:03d}" for n in range(50, 60)]
    assert set(contracts["by_id"]) == set(contracts["expiring"])
    assert contracts["by_id"]["c-000"]["supplier"] == "Alderbrook"
    assert seen[0][0] == "list_contracts"
    ending = dt.date.fromisoformat(seen[0][1]["ending_before"])
    assert ending == dt.datetime.now(dt.UTC).date() + dt.timedelta(days=90)


def test_no_contract_is_zero_expiring_and_exit_zero(workspace: Path) -> None:
    assert main(["renewals", "--within-days", "30"], relay=lambda o, a: {"contracts": []}) == 0
    done = _job(workspace)
    assert done["facts"] == {"contracts_expiring": 0, "contracts_left_out": 0}
    assert done["outputs"]["contracts"] == {"expiring": [], "by_id": {}, "left_out": []}


def test_the_same_end_date_is_ordered_by_id(workspace: Path) -> None:
    rows = [{"id": "b", "end_date": "2026-12-01"}, {"id": "a", "end_date": "2026-12-01"}]
    assert main(["renewals", "--within-days", "30"], relay=lambda o, a: {"contracts": rows}) == 0
    assert _job(workspace)["outputs"]["contracts"]["expiring"] == ["a", "b"]


def test_a_relay_that_fails_is_a_non_zero_exit(workspace: Path) -> None:
    def relay(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise RelayError("purchasing__list_contracts: down")

    assert main(["renewals", "--within-days", "30"], relay=relay) != 0
    assert not (workspace / ".choregos" / "job.json").exists()


@pytest.mark.parametrize("bad", ["0", "101", "-1"])
def test_max_above_the_spawn_ceiling_or_below_one_is_a_broken_contract(workspace: Path, bad: str) -> None:
    assert main(["renewals", "--within-days", "30", "--max", bad], relay=lambda o, a: {"contracts": []}) == 2


def _decision_relay(notes: dict[str, list[dict[str, str]]], seen: list[str]) -> Any:
    rows = [
        {"id": i, "end_date": f"2026-12-0{n}", "note_count": len(v)}
        for n, (i, v) in enumerate(notes.items(), 1)
    ]

    def relay(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if operation == "get_contract":
            seen.append(arguments["contract_id"])
            return {"contract": {"id": arguments["contract_id"], "notes": notes[arguments["contract_id"]]}}
        return {"contracts": rows}

    return relay


def test_skip_decided_leaves_out_a_contract_decided_in_the_window(workspace: Path) -> None:
    today = dt.datetime.now(dt.UTC).date()
    recent = (today - dt.timedelta(days=10)).isoformat() + "T09:00:00Z"
    old = (today - dt.timedelta(days=60)).isoformat() + "T09:00:00Z"
    notes = {
        "c-1": [{"text": "Renewal decision: renew for a year.", "created_at": recent}],
        "c-2": [{"text": "Renewal decision: renew.", "created_at": old}],
        "c-3": [{"text": "Called the supplier.", "created_at": recent}],
        "c-4": [],
    }
    seen: list[str] = []
    relay = _decision_relay(notes, seen)
    assert main(["renewals", "--within-days", "30", "--skip-decided"], relay=relay) == 0
    done = _job(workspace)
    assert done["outputs"]["contracts"]["expiring"] == ["c-2", "c-3", "c-4"]
    assert done["outputs"]["contracts"]["already_decided"] == ["c-1"]
    assert "c-1" not in done["outputs"]["contracts"]["by_id"]
    assert done["facts"] == {"contracts_expiring": 3, "contracts_left_out": 0, "contracts_already_decided": 1}
    assert sorted(seen) == ["c-1", "c-2", "c-3"]  # c-4 has no note: get_contract is not asked


def test_without_the_flag_the_output_and_the_facts_are_unchanged(workspace: Path) -> None:
    recent = dt.datetime.now(dt.UTC).date().isoformat() + "T09:00:00Z"
    seen: list[str] = []
    relay = _decision_relay({"c-1": [{"text": "Renewal decision: renew.", "created_at": recent}]}, seen)
    assert main(["renewals", "--within-days", "30"], relay=relay) == 0
    done = _job(workspace)
    assert done["facts"] == {"contracts_expiring": 1, "contracts_left_out": 0}
    assert done["outputs"]["contracts"] == {
        "expiring": ["c-1"],
        "by_id": {"c-1": {"id": "c-1", "end_date": "2026-12-01", "note_count": 1}},
        "left_out": [],
    }
    assert seen == []
