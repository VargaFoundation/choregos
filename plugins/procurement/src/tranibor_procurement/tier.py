# SPDX-License-Identifier: Apache-2.0
"""`tier`: the approval tier, derived from a threshold the workflow writes (ADR 0202)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from tranibor_procurement import job
from tranibor_procurement.compare import AWARD_BASIS, as_number, read_json, to_decimal

COMMITTEE: Final = "committee"
STANDARD: Final = "standard"


def parse_threshold(text: str) -> Decimal:
    """The `--committee-from` amount; anything but a finite number is refused, there is no default."""
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        raise ValueError(f"{text!r} is not a number") from None
    if not value.is_finite():
        raise ValueError(f"{text!r} is not a finite number")
    return value


def tier(root: Path, committee_from: Decimal) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Returns (summary, facts, outputs) for the award basis under `.choregos/inputs/`."""
    basis = read_json(job.inputs_dir(root), AWARD_BASIS)
    if not isinstance(basis, dict) or basis.get("highest_total") is None:
        raise job.ContractError(f"{AWARD_BASIS} has no highest_total: there is nothing to tier")
    highest = to_decimal(basis["highest_total"], f"{AWARD_BASIS}.highest_total")
    chosen = COMMITTEE if highest >= committee_from else STANDARD
    summary = (
        f"Approval tier {chosen}: highest total {highest} against a committee threshold of {committee_from}."
    )
    facts = {"approval_tier_threshold": as_number(committee_from)}
    outputs = {"approval_tier": chosen, "highest_total": as_number(highest)}
    return summary, facts, outputs
