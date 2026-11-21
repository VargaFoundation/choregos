# SPDX-License-Identifier: Apache-2.0
"""`tranibor-procurement tier`: the approval tier from a threshold the workflow writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tranibor_procurement.cli import main
from tranibor_procurement.compare import input_filename


def _basis(root: Path, highest: object) -> None:
    doc = {"totals": {}, "lowest_total": 1, "highest_total": highest, "spread": 0, "mismatches": []}
    (root / ".choregos" / "inputs" / input_filename("award_basis")).write_text(json.dumps(doc))


def _job(root: Path) -> dict[str, object]:
    done: dict[str, object] = json.loads((root / ".choregos" / "job.json").read_text(encoding="utf-8"))
    return done


@pytest.mark.parametrize(
    ("highest", "tier"), [(10000.00, "committee"), (10000.01, "committee"), (9999.99, "standard")]
)
def test_the_threshold_itself_is_the_committee(workspace: Path, highest: float, tier: str) -> None:
    _basis(workspace, highest)
    assert main(["tier", "--committee-from", "10000"]) == 0
    done = _job(workspace)
    assert done["outputs"] == {"approval_tier": tier, "highest_total": highest}
    assert done["facts"] == {"approval_tier_threshold": 10000.0}


def test_no_threshold_is_a_failure_not_the_lighter_tier(workspace: Path) -> None:
    _basis(workspace, 1)
    with pytest.raises(SystemExit) as exc:
        main(["tier"])
    assert exc.value.code != 0
    assert not (workspace / ".choregos" / "job.json").exists()


@pytest.mark.parametrize("bad", ["", "abc", "nan", "inf"])
def test_a_non_numeric_threshold_is_a_failure(workspace: Path, bad: str) -> None:
    _basis(workspace, 1)
    with pytest.raises(SystemExit) as exc:
        main(["tier", "--committee-from", bad])
    assert exc.value.code != 0
    assert not (workspace / ".choregos" / "job.json").exists()


def test_an_award_basis_without_a_total_is_a_broken_contract(workspace: Path) -> None:
    _basis(workspace, None)
    assert main(["tier", "--committee-from", "10"]) == 2
