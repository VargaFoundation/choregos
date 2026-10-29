# SPDX-License-Identifier: Apache-2.0
"""The composite: several providers behind one connector, with a second opinion.

Design choice: the registry builds ONE adapter from ONE connector's config and secrets; it cannot
wire a connector on top of other connectors of the organisation. So the composite lists provider
TYPES (`sources: ["stooq", "yahoo"]`), each with its own sub-config in `source_configs`, and takes
their secrets as flat fields named `<type>_api_key` (`stooq_api_key`, `alphavantage_api_key`,
`tiingo_api_key`) —
resolved API-side like any secret reference, never stored in the config.

`get_bars` tries the sources in order, falling back on an error or an empty answer. The first
source that answers with bars is the PRIMARY. With `cross_check`, the next source that also
answers is compared with it on the closes of their common dates:

    cross_check: {source, common_days, max_rel_diff, diverging_days}

where a day diverges when |a - b| / |a| > `tolerance` (default 2 %). The comparison is about the
raw `close`: it reports a split one source missed or a bad print, it does not choose a winner —
the caller decides what to do with a divergence. `fallbacks` lists the sources that failed.

A source without the full history (`full_history` false: Alpha Vantage's free key in `compact`)
is never the primary, wherever it sits in `sources`: half a series would pass for the whole one.
It still cross-checks the primary on the days both have, and `common_days` says how many (S38-21).
"""

from __future__ import annotations

from typing import Any

from .base import MarketDataError, MarketDataProvider, UnknownSymbol, validate_range, validate_symbol


def compare_closes(
    primary: list[dict[str, Any]], other: list[dict[str, Any]], tolerance: float
) -> dict[str, Any]:
    """Closes compared on the dates both have: how many, the worst relative gap, how many exceed."""
    autres = {b["date"]: b["close"] for b in other}
    communs = [(b["close"], autres[b["date"]], b["date"]) for b in primary if b["date"] in autres]
    ecarts = [(abs(a - b) / abs(a) if a else (0.0 if b == 0 else float("inf")), d) for a, b, d in communs]
    divergents = [d for e, d in ecarts if e > tolerance]
    return {
        "common_days": len(communs),
        "max_rel_diff": max((e for e, _ in ecarts), default=0.0),
        "diverging_days": len(divergents),
        "diverging_dates": divergents[:10],
    }


class CompositeProvider(MarketDataProvider):
    name = "composite"

    def __init__(
        self,
        sources: list[tuple[str, MarketDataProvider]],
        *,
        cross_check: bool = True,
        tolerance: float = 0.02,
    ) -> None:
        if not sources:
            raise MarketDataError("composite: at least one source is required")
        if tolerance < 0:
            raise MarketDataError("composite: tolerance cannot be negative")
        self._sources = sources
        self._cross_check = cross_check
        self._tolerance = tolerance

    async def aclose(self) -> None:
        for _, source in self._sources:
            await source.aclose()

    async def get_bars(self, symbol: str, start: str, end: str) -> dict[str, Any]:
        validate_symbol(symbol)
        validate_range(start, end)
        primaire: dict[str, Any] | None = None
        verification: dict[str, Any] | None = None
        vide: dict[str, Any] | None = None
        echecs: list[dict[str, str]] = []
        partielle: tuple[str, dict[str, Any]] | None = None
        inconnus = 0
        for nom, source in self._sources:
            try:
                reponse = await source.get_bars(symbol, start, end)
            except MarketDataError as erreur:
                inconnus += isinstance(erreur, UnknownSymbol)
                echecs.append({"source": nom, "error": str(erreur)})
                continue
            if not reponse["bars"]:
                vide = vide or reponse
                echecs.append({"source": nom, "error": "empty"})
                continue
            if primaire is None and not source.full_history:
                # Recent days only: kept to check the primary once one answers, never the bars.
                partielle = partielle or (nom, reponse)
                continue
            if primaire is None:
                primaire = reponse
                if not self._cross_check:
                    break
                if partielle is None:
                    continue
                verificateur, autre = partielle
            else:
                verificateur, autre = nom, reponse
            verification = {
                "source": verificateur,
                **compare_closes(primaire["bars"], autre["bars"], self._tolerance),
            }
            break
        if primaire is None:
            if partielle is not None and vide is None:
                # Not an unknown symbol: a source knows it, but only its recent days.
                raise MarketDataError(
                    f"composite: only {partielle[0]} answered, and it has the recent days only"
                )
            if vide is not None:
                return {**vide, "cross_check": None, "fallbacks": echecs}
            detail = "; ".join(f"{e['source']}: {e['error']}" for e in echecs)
            fabrique = UnknownSymbol if inconnus == len(echecs) else MarketDataError
            raise fabrique(f"composite: no source answered ({detail})")
        resultat = {**primaire, "cross_check": verification if self._cross_check else None}
        if echecs:
            resultat["fallbacks"] = echecs
        return resultat

    async def get_instrument(self, symbol: str) -> dict[str, Any]:
        validate_symbol(symbol)
        echecs: list[str] = []
        reserve: dict[str, Any] | None = None
        for nom, source in self._sources:
            try:
                reponse = await source.get_instrument(symbol)
            except MarketDataError as erreur:
                echecs.append(f"{nom}: {erreur}")
                continue
            # A source that cannot tell the type (stooq) is not the last word if another can.
            if reponse.get("type") == "unknown":
                reserve = reserve or reponse
                continue
            return reponse
        if reserve is not None:
            return reserve
        raise UnknownSymbol(f"composite: no source knows {symbol} ({'; '.join(echecs)})")

    async def test(self) -> dict[str, Any]:
        return {"ok": True, "provider": self.name, "sources": [nom for nom, _ in self._sources]}
