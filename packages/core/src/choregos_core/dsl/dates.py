# SPDX-License-Identifier: Apache-2.0
"""A date drawn from a work item field (S20-05): `fields.arrival_date - 10d`.

A pure function, with no clock: the interpreter recomputes it each time a field changes, and
compares it with ITS time (`workflow.now()`), which is what makes it replayable. A bare date
(`2026-11-02`) means midnight UTC; a date-time keeps its zone, and without a zone it is UTC.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

_EXPRESSION = re.compile(r"^fields\.([a-z_][a-z0-9_]{0,62})(?:\s*([+-])\s*([0-9]{1,4})\s*([dhm]))?$")
_UNITS = {"d": "days", "h": "hours", "m": "minutes"}


class UnreadableDate(ValueError):  # noqa: N818 - a named refusal
    """The field exists but does not say a date."""


def field_of(expression: str) -> str:
    """The field an expression reads: `arrival_date` for `fields.arrival_date - 10d`."""
    found = _EXPRESSION.fullmatch(expression.strip())
    if found is None:
        raise ValueError(f"unreadable date expression: {expression!r} (expected: `fields.<field> - 10d`)")
    return found.group(1)


def due_date(expression: str, fields: dict[str, Any]) -> datetime | None:
    """The date the expression gives for these fields, or nothing when the field is not (yet)
    filled in: we then wait for it to be, we do not leave without a date."""
    found = _EXPRESSION.fullmatch(expression.strip())
    if found is None:
        raise ValueError(f"unreadable date expression: {expression!r} (expected: `fields.<field> - 10d`)")
    name, sign, quantity, unit = found.groups()
    value = fields.get(name)
    if value in (None, ""):
        return None
    base = _instant(name, value)
    if sign is None:
        return base
    offset = timedelta(**{_UNITS[unit]: int(quantity)})
    return base - offset if sign == "-" else base + offset


def _instant(name: str, value: Any) -> datetime:
    text = str(value).strip()
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            return datetime(day.year, day.month, day.day, tzinfo=UTC)
        instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise UnreadableDate(f"the field `{name}` is not a date: {text!r}") from error
    return instant if instant.tzinfo is not None else instant.replace(tzinfo=UTC)


# Former names, still imported outside core until their own stories switch them (ADR 0098 §3 (b)).
DateIllisible = UnreadableDate  # lang: fr-ok alias:internal
champ_de = field_of  # lang: fr-ok alias:internal
echeance = due_date  # lang: fr-ok alias:internal
