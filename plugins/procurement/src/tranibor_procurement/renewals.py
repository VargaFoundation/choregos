# SPDX-License-Identifier: Apache-2.0
"""`renewals`: the contracts that end soon, as the fan-out of a renewal campaign needs them (ADR 0198).

Calls `purchasing.list_contracts` (`ending_before` an ISO date) through the relay. Output `contracts` =
`{expiring: [id], by_id: {id: summary}, left_out: [id]}`, ordered by end date then id: `--max` caps the
children a campaign spawns (ADR 0057) and what does not fit is named in `left_out`, never dropped.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Final

from tranibor_procurement import job
from tranibor_procurement.relay import Relay, collect

OUTPUT_NAME: Final = "contracts"
#: The spawn ceiling of ADR 0057.
MAX_CEILING: Final = 100
#: The contract summary's key that holds its end date, as the operation returns it.
END_DATE: Final = "end_date"


def renewals(
    relay: Relay, within_days: int, maximum: int, today: dt.date | None = None
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Returns (summary, facts, outputs)."""
    if within_days < 0:
        raise job.ContractError("--within-days must not be negative")
    if not 1 <= maximum <= MAX_CEILING:
        raise job.ContractError(f"--max must be between 1 and {MAX_CEILING}")
    start = today or dt.datetime.now(dt.UTC).date()
    cutoff = start + dt.timedelta(days=within_days)
    rows = collect(relay, "list_contracts", {"ending_before": cutoff.isoformat()}, "contracts")
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        identifier = row.get("id")
        if not isinstance(identifier, str | int) or isinstance(identifier, bool) or identifier == "":
            raise job.ContractError("a contract of list_contracts has no id")
        by_id[str(identifier)] = row
    # A contract with no end date goes last: it is not known to end, but it was asked about.
    ordered = sorted(by_id, key=lambda i: (str(by_id[i].get(END_DATE) or "9999-12-31"), i))
    expiring, left_out = ordered[:maximum], ordered[maximum:]
    contracts = {"expiring": expiring, "by_id": {i: by_id[i] for i in expiring}, "left_out": left_out}
    facts = {"contracts_expiring": len(expiring), "contracts_left_out": len(left_out)}
    summary = (
        f"{len(expiring)} contract(s) end before {cutoff.isoformat()}"
        + (f", {len(left_out)} left out past --max {maximum}" if left_out else "")
        + "."
    )
    return summary, facts, {OUTPUT_NAME: contracts}
