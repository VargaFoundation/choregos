# SPDX-License-Identifier: Apache-2.0
"""`compare`: every number a comparison memo may quote, in decimal arithmetic (ADR 0198).

Reads `.choregos/inputs/offers_structured.json` (the shape of `schemas/offers_structured.schema.json`),
`offers_text.json` and the field `currency`. A runner mounts an inline JSON input as its artifact name
plus the type's extension; `input_filename` is that rule.
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any, Final

from tranibor_procurement import job
from tranibor_procurement.schemas import OFFERS_STRUCTURED

OFFERS_TEXT: Final = "offers_text"
AWARD_BASIS: Final = "award_basis"
CENT: Final = Decimal("0.01")
#: A line whose product differs from its written total by more than this is a mismatch.
TOLERANCE: Final = Decimal("0.01")


def input_filename(name: str) -> str:
    """The file an inline JSON artifact is mounted under (`filename_of`: name plus the type's extension)."""
    return name if name.lower().endswith(".json") else f"{name}.json"


def money(value: Decimal) -> Decimal:
    """Two places, half-even."""
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def as_number(value: Decimal) -> float:
    """An amount as a JSON number: the only conversion out of `Decimal`, after all the arithmetic."""
    return float(money(value))


def to_decimal(value: object, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise job.ContractError(f"{where} is not a number")
    return Decimal(value)


def read_json(inputs: Path, name: str) -> Any:
    """An input document; its numbers are read as `Decimal`, never as floats."""
    path = inputs / input_filename(name)
    if not path.is_file():
        raise job.ContractError(f"the input {path.name} is missing from {inputs}")
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, ValueError) as exc:
        raise job.ContractError(f"{path} is unreadable: {exc}") from exc


def _optional(value: object, where: str) -> Decimal | None:
    return None if value is None else to_decimal(value, where)


def _object(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise job.ContractError(f"{where} is not an object")
    return value


def _list(value: object, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise job.ContractError(f"{where} is not a list")
    return value


def _supplier(raw: object, index: int) -> dict[str, Any]:
    where = f"suppliers[{index}]"
    data = _object(raw, where)
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise job.ContractError(f"{where}.name is not a text")
    lines_total = Decimal(0)
    without_total = 0
    any_total = False
    mismatches: list[dict[str, Any]] = []
    for i, raw_line in enumerate(_list(data.get("lines", []), f"{where}.lines")):
        at = f"{where}.lines[{i}]"
        line = _object(raw_line, at)
        total = _optional(line.get("line_total"), f"{at}.line_total")
        quantity = _optional(line.get("quantity"), f"{at}.quantity")
        price = _optional(line.get("unit_price"), f"{at}.unit_price")
        if total is None:
            without_total += 1
        else:
            any_total = True
            lines_total += total
        if total is not None and quantity is not None and price is not None:
            product = quantity * price
            if abs(product - total) > TOLERANCE:
                mismatches.append(
                    {"label": line.get("label"), "expected": money(product), "written": money(total)}
                )
    lines_total = money(lines_total)
    stated = _optional(data.get("stated_total"), f"{where}.stated_total")
    total_mismatch: dict[str, Decimal] | None = None
    if stated is not None and any_total and abs(stated - lines_total) > TOLERANCE:
        total_mismatch = {"expected": lines_total, "written": money(stated)}
    currency = data.get("currency")
    files = [str(f) for f in _list(data.get("files", []), f"{where}.files")]
    return {
        "name": name,
        "currency": currency if isinstance(currency, str) and currency else None,
        "files": files,
        "lines_total": lines_total,
        "lines_without_total": without_total,
        "line_mismatches": mismatches,
        "total_mismatch": total_mismatch,
        "stated_total": None if stated is None else money(stated),
        "total": money(stated) if stated is not None else lines_total,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return as_number(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def compare(root: Path) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Returns (summary, facts, outputs) for the offers under `.choregos/inputs/`."""
    inputs = job.inputs_dir(root)
    doc = _object(read_json(inputs, OFFERS_STRUCTURED), OFFERS_STRUCTURED)
    text = _object(read_json(inputs, OFFERS_TEXT), OFFERS_TEXT)
    suppliers = [_supplier(s, i) for i, s in enumerate(_list(doc.get("suppliers", []), "suppliers"))]
    suppliers.sort(key=lambda s: (s["total"], s["name"]))
    ranking = [{"rank": i, "supplier": s["name"], "total": s["total"]} for i, s in enumerate(suppliers, 1)]
    totals = [s["total"] for s in suppliers]
    lowest = min(totals) if totals else None
    highest = max(totals) if totals else None
    spread = money(highest - lowest) if lowest is not None and highest is not None else None
    ratio = (
        (spread / lowest).quantize(CENT, rounding=ROUND_HALF_EVEN) if spread is not None and lowest else None
    )
    mismatches: list[dict[str, Any]] = []
    for s in suppliers:
        for m in s["line_mismatches"]:
            mismatches.append(
                {
                    "supplier": s["name"],
                    "kind": "line_total",
                    "expected": m["expected"],
                    "written": m["written"],
                }
            )
        if s["total_mismatch"]:
            mismatches.append({"supplier": s["name"], "kind": "stated_total", **s["total_mismatch"]})

    claimed = {f for s in suppliers for f in s["files"]}
    extracted = [
        str(_object(f, f"{OFFERS_TEXT}.files[{i}]").get("name"))
        for i, f in enumerate(_list(text.get("files", []), f"{OFFERS_TEXT}.files"))
    ]
    unassigned = sorted(
        {f for f in extracted if f not in claimed}
        | {str(f) for f in _list(doc.get("unassigned_files", []), "unassigned_files")}
    )

    request_currency = job.read_fields(root).get("currency")
    known = {s["currency"] for s in suppliers if s["currency"]}
    if isinstance(request_currency, str) and request_currency:
        known.add(request_currency)
    currencies = len(known) + sum(1 for s in suppliers if not s["currency"])

    comparison = {
        "suppliers": suppliers,
        "ranking": ranking,
        "lowest_total": lowest,
        "highest_total": highest,
        "spread": spread,
        "spread_ratio": ratio,
    }
    award_basis = {
        "totals": {s["name"]: s["total"] for s in suppliers},
        "lowest_total": lowest,
        "highest_total": highest,
        "spread": spread,
        "mismatches": mismatches,
    }
    facts: dict[str, Any] = {
        "suppliers_count": len(suppliers),
        "files_unassigned": len(unassigned),
        "currencies": currencies,
        "mismatches": len(mismatches),
    }
    if lowest is not None and highest is not None:
        facts["lowest_total"] = as_number(lowest)
        facts["highest_total"] = as_number(highest)
    summary = (
        f"{len(suppliers)} suppliers compared"
        + (f", totals from {lowest} to {highest}" if lowest is not None else "")
        + f"; {len(mismatches)} mismatches, {len(unassigned)} unassigned files, {currencies} currencies."
    )
    outputs = {"comparison": _jsonable(comparison), "award_basis": _jsonable(award_basis)}
    return summary, facts, outputs
