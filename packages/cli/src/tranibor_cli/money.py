# SPDX-License-Identifier: Apache-2.0
"""Every amount in the organisation's currency (ADR 0187 §1, S67-25).

The organisation's `currency` setting (`EUR` or `USD`, S29-03) is read once per process and organisation
(`GET /orgs`), then every amount is printed with its symbol. The API serves some amounts in both currencies
(a cost report's total, a work item's totals), some in one only (a project's month in euros, a run's spend in
dollars) and states no exchange rate. So an amount is taken in the organisation's currency when the API gives
it; otherwise it is printed in the one it has, **with its own symbol, never relabelled**. Unknown is "—",
never 0.
"""

from __future__ import annotations

from dataclasses import dataclass

from .client import ApiError, Client

DASH = "—"
SYMBOLS = {"EUR": "€", "USD": "$"}

#: `(API url, organisation)` -> its currency, or `None` when the API would not say: one read per process.
_CURRENCIES: dict[tuple[str, str], str | None] = {}


def forget() -> None:
    """Drop what was read (tests, and a process that switches profile)."""
    _CURRENCIES.clear()


def currency_of(api: Client, organisation: str) -> str | None:
    """The organisation's currency; `None` when it cannot be read (then every amount keeps its own symbol)."""
    key = (api.profile.api_url, organisation)
    if key not in _CURRENCIES:
        found: str | None = None
        try:
            for org in api.get("/orgs", quiet=True) or []:
                if organisation == org.get("slug") or organisation in (org.get("aliases") or []):
                    value = org.get("currency") or "EUR"  # the setting's default (S29-03)
                    found = value if value in SYMBOLS else None
                    break
        except ApiError:
            found = None
        _CURRENCIES[key] = found
    return _CURRENCIES[key]


@dataclass(frozen=True)
class Amount:
    """What the API gave for one figure: its dollars, its euros, either or both (`None` is unknown)."""

    usd: float | None = None
    eur: float | None = None

    def text(self, currency: str | None, places: int = 2) -> str:
        """`€1.24`: the organisation's currency when the API gave it, else the other one with its symbol."""
        order = ("EUR", "USD") if currency != "USD" else ("USD", "EUR")
        if currency is None:  # unknown preference: whatever is given, in its own symbol
            order = ("USD", "EUR") if self.eur is None else ("EUR", "USD")
        for code in order:
            value = self.eur if code == "EUR" else self.usd
            if isinstance(value, int | float) and not isinstance(value, bool):
                return f"{SYMBOLS[code]}{float(value):.{places}f}"
        return DASH


def amount(source: object, *, usd: str = "cost_usd", eur: str = "cost_eur") -> Amount:
    """An `Amount` read from a payload's keys (a missing or null key is unknown)."""
    if not isinstance(source, dict):
        return Amount()
    return Amount(usd=_number(source.get(usd)), eur=_number(source.get(eur)))


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
