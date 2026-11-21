# SPDX-License-Identifier: Apache-2.0
"""The notation of a document's amounts, and their rewrite to plain decimal-point form."""

from __future__ import annotations

import pytest
from tranibor_procurement.numbers import detect, normalise, settle, settle_table

#: A decimal-comma context, so that the amount under test is read the way a French offer writes it.
CONTEXT = "Total 1 000,50 EUR, tax 200,00 EUR. "


@pytest.mark.parametrize(
    ("written", "plain"),
    [
        ("12 500,50", "12500.50"),
        ("12 500,50", "12500.50"),
        ("12 500,50", "12500.50"),
        ("12.500,50", "12500.50"),
        ("1 234 567,00", "1234567.00"),
        ("1.234.567,00", "1234567.00"),
        ("0,5", "0.5"),
        ("125,50", "125.50"),
        ("12,500.50", "12,500.50"),  # decimal-point form is left as it is, even in a decimal-comma file
        ("12.50", "12.50"),
    ],
)
def test_an_amount_of_a_decimal_comma_file_is_rewritten(written: str, plain: str) -> None:
    text, notation = settle(f"{CONTEXT}Amount: {written} EUR.")
    assert notation == "decimal_comma"
    assert text == f"Total 1000.50 EUR, tax 200.00 EUR. Amount: {plain} EUR."


@pytest.mark.parametrize(
    "untouched",
    [
        "2026-11-20",
        "20/11/2026",
        "20.11.2026",
        "12:30",
        "FR7630006000011234567890189",  # a 27-character bank-account-shaped identifier
        "FR76 3000 6000 0112 3456 7890 189",
        "+33 1 23 45 67 89",
        "+33 123 456 789",
        "0123 456 789",
        "1234567890123456789",  # more than 15 digits
        "REF-12 500",
        "A12,50",
        "12,50EUR",
    ],
)
def test_a_date_a_phone_number_and_an_identifier_are_not_rewritten(untouched: str) -> None:
    text, notation = settle(f"{CONTEXT}See {untouched} for details.")
    assert notation == "decimal_comma"
    assert text == f"Total 1000.50 EUR, tax 200.00 EUR. See {untouched} for details."


def test_a_decimal_point_file_is_left_as_written() -> None:
    written = "Total 12,500.50 EUR, unit 1,250.25 and 0,5 kg"
    assert settle(written) == (written, "decimal_point")


@pytest.mark.parametrize("written", ["12,50 and 12.50", "no amount here at all", "", "100 and 2026-11-20"])
def test_a_tie_or_no_evidence_is_ambiguous_and_the_file_is_left_as_written(written: str) -> None:
    assert detect(written) == "ambiguous"
    assert settle(written) == (written, "ambiguous")


def test_the_majority_wins() -> None:
    assert detect("1 000,50 and 2 000,50 and 3,000.50") == "decimal_comma"
    assert detect("1,000.50 and 2,000.50 and 3 000,50") == "decimal_point"


@pytest.mark.parametrize(
    ("written", "forced", "expected"),
    [
        ("Price 12 500,50 and 3,25", "decimal_point", "Price 12 500,50 and 3,25"),  # forces "leave it"
        ("Price 12,500.50 and 0,5", "decimal_comma", "Price 12,500.50 and 0.5"),  # forces a rewrite
        ("0,5 only", "auto", "0,5 only"),  # nothing to decide from
        ("0,5 only", "decimal_comma", "0.5 only"),
    ],
)
def test_number_format_overrides_the_detection(written: str, forced: str, expected: str) -> None:
    text, notation = settle(written, forced)
    assert text == expected
    assert notation == (forced if forced != "auto" else "ambiguous")


def test_an_unknown_number_format_is_refused() -> None:
    with pytest.raises(ValueError, match="number_format"):
        settle("1", "european")
    with pytest.raises(ValueError, match="number_format"):
        settle_table("1", "european")


def test_normalise_only_rewrites_a_decimal_comma_notation() -> None:
    assert normalise("12 500,50", "ambiguous") == "12 500,50"
    assert normalise("12 500,50", "decimal_point") == "12 500,50"


def test_a_delimited_file_is_read_cell_by_cell() -> None:
    csv_text = 'item;qty;total\nbracket;400;"5 000,50"\nbolt;10;"12,25"\n'
    assert settle_table(csv_text) == ("item;qty;total\nbracket;400;5000.50\nbolt;10;12.25\n", "decimal_comma")
    commas = "item,qty,total\nbracket,400,5000.00\nbolt,10,12.50\n"
    assert settle_table(commas) == (commas, "decimal_point")


def test_a_clause_number_at_the_start_of_a_line_is_not_evidence() -> None:
    clauses = "3.2 Delivery\n3.3 Payment\n3.4 Warranty\nTotal 1 000,50 EUR\n"
    assert settle(clauses) == (clauses.replace("1 000,50", "1000.50"), "decimal_comma")
