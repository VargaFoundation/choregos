# SPDX-License-Identifier: Apache-2.0
"""One notation for the amounts of a document (ADR 0198 point 3).

An amount is written `12 500,50` as readily as `12,500.50`, and the `numbers_measured` gate reads the
second notation only. Per file, `detect` decides the notation from the amounts the text holds, and
`normalise` rewrites a decimal-comma file to plain decimal-point form, without thousands separators.
A file it cannot decide is left as written and counted by the caller.

What is never an amount: a date, a time, a phone number (a `+` in front), an identifier (a run of more
than 15 digits, a number glued to letters, an IBAN-shaped block), a number that is one group of a longer
run of digits. They are left as they are, whatever the notation.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Final, Literal

Notation = Literal["decimal_comma", "decimal_point", "ambiguous"]
NUMBER_FORMATS: Final = ("auto", "decimal_comma", "decimal_point")

_SPACES = "   "
#: Every shape an amount can take in either notation; `(?!\w)` and `(?<!\w)` keep out a number glued to
#: letters or to more digits. The first match wins, so a grouped number is read before a plain one.
_NUMBER = re.compile(
    rf"(?<!\w)(?:"
    rf"\d{{1,3}}(?:[{_SPACES}]\d{{3}})+(?:[.,]\d+)?"  # 12 500,50  1 234 567
    rf"|\d{{1,3}}(?:\.\d{{3}})+(?:,\d+)?"  # 12.500,50  1.234.567
    rf"|\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?"  # 12,500.50
    rf"|\d+(?:[.,]\d+)?"  # 0,5  12.50  1234
    rf")(?!\w)"
)
#: A bank account number written in blocks (an IBAN, 15 to 34 characters): never an amount.
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
_COMMA_EVIDENCE = re.compile(rf"\d{{1,3}}(?:(?:[{_SPACES}]|\.)\d{{3}})*,\d{{2}}")
_POINT_EVIDENCE = re.compile(r"\d{1,3}(?:,\d{3})*\.\d{1,2}")
_SECTION_NUMBER = re.compile(r"[#>*\-\s]*(\d+\.\d)\s+[^\W\d_]")
_MAX_DIGITS = 15


def _digits(token: str) -> int:
    return sum(c.isdigit() for c in token)


def _grouped(token: str) -> bool:
    return any(c in _SPACES for c in token)


def _skipped(text: str, m: re.Match[str]) -> bool:
    """Whether the number `m` is a date, a time, a phone number, a clause number or part of a longer run."""
    start, end = m.span()
    before = text[start - 1] if start else ""
    before2 = text[start - 2] if start > 1 else ""
    after = text[end] if end < len(text) else ""
    after2 = text[end + 1] if end + 1 < len(text) else ""
    if before and before in "/:+@#":
        return True  # a date, a time, a phone number, a handle
    if before == "-" and (before2.isalnum() or before2 == "-"):
        return True  # 2026-11-20, AB-123
    if before in {".", ","} and before2.isdigit():
        return True  # the tail of a longer run
    if before and before in _SPACES and before2.isdigit() and _grouped(m.group()):
        return True  # the groups of a longer run of digits (a phone number written 0123 456 789)
    if after and after in "/:":
        return True
    if after == "-" and after2.isdigit():
        return True
    if after in {".", ","} and after2.isdigit():
        return True
    line_start = text.rfind("\n", 0, start) + 1
    lead = _SECTION_NUMBER.match(text, line_start)
    return lead is not None and start == lead.start(1) and "," not in m.group()  # `3.2 Delivery`


def _amounts(text: str) -> list[re.Match[str]]:
    """The numbers of `text` that may be amounts: not dates, phone numbers, identifiers or clause numbers."""
    blocked = [m.span() for m in _IBAN.finditer(text)]
    return [
        m
        for m in _NUMBER.finditer(text)
        if not any(a <= m.start() < b for a, b in blocked)
        and _digits(m.group()) <= _MAX_DIGITS
        and not _skipped(text, m)
    ]


def detect(text: str) -> Notation:
    """The notation the amounts of `text` are written in; a tie, or no evidence, is `ambiguous`."""
    comma = point = 0
    for m in _amounts(text):
        token = m.group()
        if _COMMA_EVIDENCE.fullmatch(token):
            comma += 1
        elif _POINT_EVIDENCE.fullmatch(token):
            point += 1
    if comma > point:
        return "decimal_comma"
    if point > comma:
        return "decimal_point"
    return "ambiguous"


def _plain(token: str) -> str:
    """A decimal-comma amount as plain decimal-point form: no thousands separator, a point."""
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+\.\d+", token):
        return token  # already in decimal-point form
    whole, _, decimals = token.partition(",")
    for sep in _SPACES + ".":
        whole = whole.replace(sep, "")
    if "," in token:
        return f"{whole}.{decimals}"
    if _grouped(token) or re.fullmatch(r"\d{1,3}(?:\.\d{3})+", token):
        return whole  # a thousands separator and nothing after it
    return token


def normalise(text: str, notation: str) -> str:
    """`text` with every amount in plain decimal-point form when it is written with a decimal comma."""
    if notation != "decimal_comma":
        return text
    out: list[str] = []
    last = 0
    for m in _amounts(text):
        out.append(text[last : m.start()])
        out.append(_plain(m.group()))
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def settle(text: str, number_format: str = "auto") -> tuple[str, Notation]:
    """The text in its one notation and the notation it was written in (`number_format` overrides)."""
    if number_format not in NUMBER_FORMATS:
        raise ValueError(f"number_format must be one of {', '.join(NUMBER_FORMATS)}, not {number_format!r}")
    notation: Notation = detect(text) if number_format == "auto" else number_format  # type: ignore[assignment]
    return normalise(text, notation), notation


def settle_table(text: str, number_format: str = "auto") -> tuple[str, Notation]:
    """`settle` for a delimited file: each cell is read alone, a delimiter is not a decimal comma."""
    if number_format not in NUMBER_FORMATS:
        raise ValueError(f"number_format must be one of {', '.join(NUMBER_FORMATS)}, not {number_format!r}")
    try:
        delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    notation: Notation = (
        detect("\n".join(cell for row in rows for cell in row)) if number_format == "auto" else number_format  # type: ignore[assignment]
    )
    if notation != "decimal_comma":
        return text, notation
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\n")
    writer.writerows([normalise(cell, notation) for cell in row] for row in rows)
    return out.getvalue(), notation
