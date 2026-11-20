# SPDX-License-Identifier: Apache-2.0
"""`supplier-check`: the measured facts of a supplier's onboarding file (ADR 0198).

Reads `.choregos/inputs/supplier_file.json` (the shape of `schemas/supplier_file.schema.json`) and the
fields `legal_name`, `registration_number` and `country` of `.choregos/fields.json`. The kinds a file
must hold are arguments the workflow writes: no kind is required by default. No bank detail is read:
only the identity and the dated documents, and a document's `identifier` is never copied out.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Final

from tranibor_procurement import job
from tranibor_procurement.compare import read_json
from tranibor_procurement.relay import CONNECTOR, Relay, collect
from tranibor_procurement.schemas import SUPPLIER_FILE

OUTPUT_NAME: Final = "supplier_check"
#: The identity fields compared with the file's, in the order the output lists them.
IDENTITY_FIELDS: Final = ("legal_name", "registration_number")


def normalise(text: str) -> str:
    """Case, spaces and punctuation do not tell two spellings apart; a digit does."""
    return "".join(c for c in text.casefold() if c.isalnum())


def parse_as_of(text: str, today: dt.date | None = None) -> dt.date:
    """`--as-of`: an ISO date, or `today`."""
    if text == "today":
        return today or dt.datetime.now(dt.UTC).date()
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{text!r} is neither an ISO date nor `today`") from None


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise job.ContractError(f"{where} is not a text")
    return value


def _date(value: object, where: str) -> dt.date | None:
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(_text(value, where))
    except ValueError:
        raise job.ContractError(f"{where} is not an ISO date") from None


def _documents(file: dict[str, Any]) -> list[dict[str, Any]]:
    raw = file.get("documents")
    if not isinstance(raw, list):
        raise job.ContractError(f"{SUPPLIER_FILE}.documents is not a list")
    documents = []
    for index, item in enumerate(raw):
        where = f"{SUPPLIER_FILE}.documents[{index}]"
        if not isinstance(item, dict):
            raise job.ContractError(f"{where} is not an object")
        documents.append(
            {
                "kind": _text(item.get("kind"), f"{where}.kind"),
                "file": _text(item.get("file"), f"{where}.file"),
                "expires_on": _date(item.get("expires_on"), f"{where}.expires_on"),
            }
        )
    return documents


def _identity(file: dict[str, Any]) -> dict[str, str]:
    identity = file.get("identity")
    if not isinstance(identity, dict):
        raise job.ContractError(f"{SUPPLIER_FILE}.identity is not an object")
    return {name: _text(identity.get(name), f"{SUPPLIER_FILE}.identity.{name}") for name in IDENTITY_FIELDS}


def _duplicates(relay: Relay, fields: dict[str, Any], written: dict[str, str]) -> list[dict[str, Any]]:
    """Suppliers the purchasing system already holds under this name or this registration number."""
    arguments: dict[str, Any] = {
        "name": written["legal_name"],
        "registration_number": written["registration_number"],
    }
    country = fields.get("country")
    if isinstance(country, str) and country:
        arguments["country"] = country
    found = collect(relay, "search_suppliers", arguments, "suppliers")
    return [
        {"id": str(row.get("id", "")), "name": str(row.get("name", ""))}
        for row in sorted(found, key=lambda row: str(row.get("id", "")))
    ]


def supplier_check(
    root: Path,
    required: list[str],
    as_of: dt.date,
    *,
    relay: Relay | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Returns (summary, facts, outputs). `relay` is given only to look for duplicates."""
    file = read_json(job.inputs_dir(root), SUPPLIER_FILE)
    if not isinstance(file, dict):
        raise job.ContractError(f"{SUPPLIER_FILE} is not an object")
    fields = job.read_fields(root)
    in_file = _identity(file)
    documents = _documents(file)
    kinds = list(dict.fromkeys(required))

    missing: list[str] = []
    expired: list[dict[str, str]] = []
    for kind in kinds:
        held = [d for d in documents if normalise(d["kind"]) == normalise(kind)]
        if not held:
            missing.append(kind)
        elif all(d["expires_on"] is not None and d["expires_on"] < as_of for d in held):
            last = max(held, key=lambda d: d["expires_on"])
            expired.append({"kind": kind, "file": last["file"], "expires_on": last["expires_on"].isoformat()})

    mismatch: list[dict[str, str]] = []
    for name in IDENTITY_FIELDS:
        given = fields.get(name)
        if not isinstance(given, str) or not given.strip():
            raise job.ContractError(f"the field {name} is missing: there is nothing to compare the file with")
        if normalise(given) != normalise(in_file[name]):
            mismatch.append({"field": name, "in_file": in_file[name], "in_request": given})

    facts: dict[str, Any] = {
        "documents_missing": len(missing),
        "documents_expired": len(expired),
        "identity_mismatch": len(mismatch),
    }
    result: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "required": kinds,
        "missing": missing,
        "expired": expired,
        "identity_mismatch": mismatch,
    }
    parts = [
        f"{len(missing)} document(s) missing" + (f" ({', '.join(missing)})" if missing else ""),
        f"{len(expired)} expired" + (f" ({', '.join(e['kind'] for e in expired)})" if expired else ""),
        f"{len(mismatch)} identity field(s) differ"
        + (f" ({', '.join(m['field'] for m in mismatch)})" if mismatch else ""),
    ]
    if relay is not None:
        duplicates = _duplicates(relay, fields, in_file)
        facts["duplicates_found"] = len(duplicates)
        result["duplicates"] = duplicates
        parts.append(f"{len(duplicates)} possible duplicate(s) in {CONNECTOR}")
    summary = f"Supplier file as of {as_of.isoformat()}: " + "; ".join(parts) + "."
    return summary, facts, {OUTPUT_NAME: result}
