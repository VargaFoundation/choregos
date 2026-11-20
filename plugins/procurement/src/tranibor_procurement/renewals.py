# SPDX-License-Identifier: Apache-2.0
"""`renewals`: the contracts that end soon, as the fan-out of a renewal campaign needs them (ADR 0198).

Calls `purchasing.list_contracts` (`ending_before` an ISO date) through the relay. Output `contracts` =
`{expiring: [id], by_id: {id: summary}, left_out: [id]}`, ordered by end date then id: `--max` caps the
children a campaign spawns (ADR 0057) and what does not fit is named in `left_out`, never dropped.

With `skip_decided` (`--skip-decided`, off by default), a contract holding a note that starts
`Renewal decision:` dated within the last `--within-days` days is left out of `expiring` and named in
`contracts.already_decided`, counted by the fact `contracts_already_decided`. The notes come from
`purchasing.get_contract`, asked only for a listed contract whose `note_count` is above 0. Without the
flag the output and the facts have neither key.
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
#: What the note that records a renewal decision starts with (`purchasing.add_contract_note`).
DECISION_PREFIX: Final = "Renewal decision:"


def _note_day(note: Any) -> dt.date | None:
    stamp = note.get("created_at") if isinstance(note, dict) else None
    try:
        return dt.date.fromisoformat(stamp[:10]) if isinstance(stamp, str) else None
    except ValueError:
        return None


def _decided(relay: Relay, identifier: str, row: dict[str, Any], since: dt.date) -> bool:
    """Whether the contract holds a decision note dated `since` or later. A note with no readable date
    does not count: the contract stays listed rather than silently dropped."""
    count = row.get("note_count")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        return False
    contract = relay("get_contract", {"contract_id": identifier}).get("contract")
    notes = contract.get("notes") if isinstance(contract, dict) else None
    if not isinstance(notes, list):
        raise job.ContractError(f"purchasing__get_contract: the answer for {identifier} holds no `notes`")
    for note in notes:
        text = note.get("text") if isinstance(note, dict) else None
        day = _note_day(note)
        if isinstance(text, str) and text.lstrip().startswith(DECISION_PREFIX) and day and day >= since:
            return True
    return False


def renewals(
    relay: Relay,
    within_days: int,
    maximum: int,
    today: dt.date | None = None,
    skip_decided: bool = False,
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
    already_decided: list[str] = []
    if skip_decided:
        since = start - dt.timedelta(days=within_days)
        already_decided = sorted(i for i in by_id if _decided(relay, i, by_id[i], since))
        for i in already_decided:
            del by_id[i]
    ordered = sorted(by_id, key=lambda i: (str(by_id[i].get(END_DATE) or "9999-12-31"), i))
    expiring, left_out = ordered[:maximum], ordered[maximum:]
    contracts: dict[str, Any] = {
        "expiring": expiring,
        "by_id": {i: by_id[i] for i in expiring},
        "left_out": left_out,
    }
    facts: dict[str, Any] = {"contracts_expiring": len(expiring), "contracts_left_out": len(left_out)}
    if skip_decided:
        contracts["already_decided"] = already_decided
        facts["contracts_already_decided"] = len(already_decided)
    summary = (
        f"{len(expiring)} contract(s) end before {cutoff.isoformat()}"
        + (f", {len(left_out)} left out past --max {maximum}" if left_out else "")
        + (f", {len(already_decided)} already decided" if already_decided else "")
        + "."
    )
    return summary, facts, {OUTPUT_NAME: contracts}
