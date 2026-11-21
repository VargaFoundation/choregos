# SPDX-License-Identifier: Apache-2.0
"""Every amount in the organisation's currency (ADR 0187 §1, S67-25).

The organisation's `currency` setting (`EUR` or `USD`, S29-03) is read once per process and organisation
(`GET /orgs`), then every amount is printed with its symbol. The API serves some amounts in both currencies
(a cost report's total, a work item's totals), some in one only (a project's month in euros, a run's spend in
dollars). An amount the API gives in the other currency only is converted with the ratio the API's own cost
report shows (what the console does); with no ratio yet it is printed in the one it has, **with its own
symbol, never relabelled**. Unknown is "—",
never 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .client import ApiError, Client

DASH = "—"
SYMBOLS = {"EUR": "€", "USD": "$"}

#: `(API url, organisation)` -> its currency, or `None` when the API would not say: one read per process.
_CURRENCIES: dict[tuple[str, str], str | None] = {}


def forget() -> None:
    """Drop what was read (tests, and a process that switches profile)."""
    _CURRENCIES.clear()
    _RATES.clear()


def currency_of(api: Client, organisation: str) -> str | None:
    """The organisation's currency; `None` when it cannot be read (then every amount keeps its own symbol)."""
    key = (api.profile.api_url, organisation)
    if key not in _CURRENCIES:
        found: str | None = None
        try:
            # the server finds it by its slug or a former one (ADR 0206): no list is read to look it up
            for org in api.get("/orgs", params={"slug": organisation, "limit": 1}, quiet=True)["items"]:
                if organisation == org.get("slug") or organisation in (org.get("aliases") or []):
                    value = org.get("currency") or "EUR"  # the setting's default (S29-03)
                    found = value if value in SYMBOLS else None
                    break
        except ApiError:
            found = None
        _CURRENCIES[key] = found
    return _CURRENCIES[key]


_RATES: dict[tuple[str, str], float | None] = {}


def rate_of(api: Client, organisation: str) -> float | None:
    """Euros per dollar as the organisation's own ledger applied them this month (the console's `usdTo`,
    `apps/web/src/lib/spend.ts`): the cost report's total carries both, so the ratio is the API's own figure.
    `None` when nothing was spent yet or the report cannot be read."""
    key = (api.profile.api_url, organisation)
    if key not in _RATES:
        found: float | None = None
        today = date.today()
        try:
            report = api.get(
                f"/orgs/{organisation}/costs",
                params={"since": today.replace(day=1).isoformat(), "until": today.isoformat()},
                quiet=True,
            )
            total = amount((report or {}).get("total"))
            if total.usd and total.eur:
                found = total.eur / total.usd
        except ApiError:
            found = None
        _RATES[key] = found
    return _RATES[key]


@dataclass(frozen=True)
class Amount:
    """What the API gave for one figure: its dollars, its euros, either or both (`None` is unknown)."""

    usd: float | None = None
    eur: float | None = None

    def text(self, currency: str | None, places: int = 2, rate: float | None = None) -> str:
        """`€1.24`: the organisation's currency when the API gave it or `rate` (euros per dollar) converts
        it; otherwise the other one with its own symbol, never relabelled."""
        order = ("EUR", "USD") if currency != "USD" else ("USD", "EUR")
        if currency is None:  # unknown preference: whatever is given, in its own symbol
            order = ("USD", "EUR") if self.eur is None else ("EUR", "USD")
        if rate:
            if currency == "EUR" and self.eur is None and self.usd is not None:
                return _show("EUR", self.usd * rate, places)
            if currency == "USD" and self.usd is None and self.eur is not None:
                return _show("USD", self.eur / rate, places)
        for code in order:
            value = self.eur if code == "EUR" else self.usd
            if value is not None:
                return _show(code, value, places)
        return DASH


def _show(code: str, value: float, places: int) -> str:
    return f"{SYMBOLS[code]}{float(value):.{places}f}"


class Pricing:
    """One organisation's currency and (read only when an amount needs it) its rate, for one command."""

    def __init__(self, api: Client, organisation: str | None) -> None:
        self.api, self.organisation = api, organisation

    @property
    def currency(self) -> str | None:
        return currency_of(self.api, self.organisation) if self.organisation else None

    def text(self, value: Amount, places: int = 2) -> str:
        currency = self.currency
        rate = None
        if self.organisation and (
            (currency == "EUR" and value.eur is None and value.usd is not None)
            or (currency == "USD" and value.usd is None and value.eur is not None)
        ):
            rate = rate_of(self.api, self.organisation)
        return value.text(currency, places, rate)


def amount(source: object, *, usd: str = "cost_usd", eur: str = "cost_eur") -> Amount:
    """An `Amount` read from a payload's keys (a missing or null key is unknown)."""
    if not isinstance(source, dict):
        return Amount()
    return Amount(usd=_number(source.get(usd)), eur=_number(source.get(eur)))


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
