# SPDX-License-Identifier: Apache-2.0
"""`tranibor-procurement compare`: the numbers of a comparison, to the cent."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tranibor_api.services.artifacts import filename_of
from tranibor_procurement.cli import main
from tranibor_procurement.compare import input_filename


def _line(label: str, qty: Any, price: Any, total: Any) -> dict[str, Any]:
    return {"label": label, "quantity": qty, "unit": "pc", "unit_price": price, "line_total": total}


def _supplier(
    name: str, currency: str | None, lines: list[dict[str, Any]], stated: Any, files: list[str]
) -> dict[str, Any]:
    return {
        "name": name,
        "currency": currency,
        "files": files,
        "lines": lines,
        "stated_total": stated,
        "payment_terms_days": None,
        "delivery_days": None,
        "validity_date": None,
        "exclusions": [],
    }


def _put(root: Path, name: str, doc: Any) -> None:
    (root / ".choregos" / "inputs" / input_filename(name)).write_text(json.dumps(doc), encoding="utf-8")


def _run(
    root: Path,
    suppliers: list[dict[str, Any]],
    *,
    files: list[str],
    unassigned: list[str] | None = None,
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _put(root, "offers_structured", {"suppliers": suppliers, "unassigned_files": unassigned or []})
    _put(root, "offers_text", {"files": [{"name": n} for n in files]})
    if fields is not None:
        (root / ".choregos" / "fields.json").write_text(json.dumps(fields), encoding="utf-8")
    assert main(["compare"]) == 0
    doc: dict[str, Any] = json.loads((root / ".choregos" / "job.json").read_text(encoding="utf-8"))
    return doc


def _three() -> list[dict[str, Any]]:
    return [
        # a line mismatch: 3 x 10.10 = 30.30, written 30.00
        _supplier(
            "Alderbrook", "EUR", [_line("a", 3, 10.10, 30.00), _line("b", 1, 70, 70)], 100.00, ["a.pdf"]
        ),
        # a total mismatch: lines add to 200.00, stated 190.50
        _supplier(
            "Brightfield", "EUR", [_line("a", 2, 50, 100), _line("b", 1, 100, 100)], 190.50, ["b.docx"]
        ),
        _supplier("Corvane", "EUR", [_line("a", 4, 75, 300)], 300, ["c.xlsx"]),
    ]


def test_three_suppliers_with_a_line_and_a_total_mismatch(workspace: Path) -> None:
    done = _run(workspace, _three(), files=["a.pdf", "b.docx", "c.xlsx"])
    out = done["outputs"]
    assert out["award_basis"] == {
        "totals": {"Alderbrook": 100.0, "Brightfield": 190.5, "Corvane": 300.0},
        "lowest_total": 100.0,
        "highest_total": 300.0,
        "spread": 200.0,
        "mismatches": [
            {"supplier": "Alderbrook", "kind": "line_total", "expected": 30.3, "written": 30.0},
            {"supplier": "Brightfield", "kind": "stated_total", "expected": 200.0, "written": 190.5},
        ],
    }
    comparison = out["comparison"]
    assert [r["supplier"] for r in comparison["ranking"]] == ["Alderbrook", "Brightfield", "Corvane"]
    assert comparison["spread_ratio"] == 2.0
    alder = comparison["suppliers"][0]
    assert alder["lines_total"] == 100.0 and alder["lines_without_total"] == 0
    assert alder["line_mismatches"] == [{"label": "a", "expected": 30.3, "written": 30.0}]
    assert alder["total_mismatch"] is None
    assert comparison["suppliers"][1]["total_mismatch"] == {"expected": 200.0, "written": 190.5}
    assert done["facts"] == {
        "suppliers_count": 3,
        "files_unassigned": 0,
        "currencies": 1,
        "mismatches": 2,
        "lowest_total": 100.0,
        "highest_total": 300.0,
    }


def test_a_supplier_with_no_stated_total_uses_its_lines(workspace: Path) -> None:
    sup = _supplier("Alderbrook", "EUR", [_line("a", 2, 5, 10), _line("b", 1, None, None)], None, [])
    done = _run(workspace, [sup], files=[])
    one = done["outputs"]["comparison"]["suppliers"][0]
    assert one["total"] == 10.0 and one["lines_without_total"] == 1 and one["total_mismatch"] is None


def test_a_file_nobody_claims_counts_as_unassigned(workspace: Path) -> None:
    sup = _supplier("Alderbrook", "EUR", [], 10, ["a.pdf"])
    done = _run(
        workspace, [sup], files=["a.pdf", "orphan.pdf", "extra.pdf"], unassigned=["extra.pdf", "lost.pdf"]
    )
    assert done["facts"]["files_unassigned"] == 3  # orphan, extra (once), lost


def test_currencies_count_each_unknown_as_one_more(workspace: Path) -> None:
    two = [_supplier("A", "EUR", [], 1, []), _supplier("B", "USD", [], 2, [])]
    assert _run(workspace, two, files=[], fields={"currency": "EUR"})["facts"]["currencies"] == 2
    unknown = [_supplier("A", "EUR", [], 1, []), _supplier("B", None, [], 2, [])]
    assert _run(workspace, unknown, files=[], fields={"currency": "EUR"})["facts"]["currencies"] == 2
    assert _run(workspace, unknown, files=[], fields={"currency": "USD"})["facts"]["currencies"] == 3


def test_equal_totals_rank_by_name_whatever_the_input_order(workspace: Path) -> None:
    names = ["Zeta", "Alpha", "Mid"]
    suppliers = [_supplier(n, "EUR", [], 50, []) for n in names]
    done = _run(workspace, suppliers, files=[])
    assert [r["supplier"] for r in done["outputs"]["comparison"]["ranking"]] == ["Alpha", "Mid", "Zeta"]
    assert done["outputs"]["comparison"]["spread"] == 0.0


def test_rounding_is_half_even_to_two_places(workspace: Path) -> None:
    # 0.125 -> 0.12 and 0.135 -> 0.14 under half-even; half-up would give 0.13 for the first.
    sups = [_supplier("A", "EUR", [], 0.125, []), _supplier("B", "EUR", [], 0.135, [])]
    done = _run(workspace, sups, files=[])
    assert done["outputs"]["award_basis"]["totals"] == {"A": 0.12, "B": 0.14}


def test_inputs_are_read_under_the_name_the_runner_mounts() -> None:
    for name in ("offers_structured", "offers_text", "award_basis"):
        row = SimpleNamespace(type="json", name=name)
        assert input_filename(name) == filename_of(row)  # type: ignore[arg-type]
