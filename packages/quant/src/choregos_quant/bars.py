# SPDX-License-Identifier: Apache-2.0
"""Bars CSV: `symbol,date,open,high,low,close,adj_close,volume`, sorted by symbol then date."""

from __future__ import annotations

import csv
import datetime as dt
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from choregos_quant.canon import num, write_text
from choregos_quant.errors import InputError

HEADER = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume"]


@dataclass(frozen=True)
class Bar:
    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    adj_close: float | None
    volume: float


@dataclass
class Series:
    """One symbol's bars. `prices` is the adjusted close, or the close when it is empty."""

    symbol: str
    dates: list[str] = field(default_factory=list)
    prices: list[float] = field(default_factory=list)
    bars: list[Bar] = field(default_factory=list)

    def add(self, bar: Bar) -> None:
        self.bars.append(bar)
        self.dates.append(bar.date)
        self.prices.append(bar.adj_close if bar.adj_close is not None else bar.close)


def parse_date(text: str, what: str = "date") -> dt.date:
    try:
        parsed = dt.date.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise InputError(f"{what}: {text!r} is not an ISO date (YYYY-MM-DD)") from exc
    if parsed.isoformat() != text:
        raise InputError(f"{what}: {text!r} is not an ISO date (YYYY-MM-DD)")
    return parsed


def _float(text: str, what: str, line: int) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise InputError(f"bars line {line}: {what} {text!r} is not a number") from exc
    if not math.isfinite(value):
        raise InputError(f"bars line {line}: {what} is not finite")
    return value


def read_bars(path: str | Path) -> dict[str, Series]:
    """Read and validate a bars file (header, ordering, positive prices)."""
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != HEADER:
            raise InputError(f"bars header must be exactly {','.join(HEADER)}")
        out: dict[str, Series] = {}
        previous: tuple[str, str] | None = None
        for lineno, row in enumerate(reader, start=2):
            if len(row) != len(HEADER):
                raise InputError(f"bars line {lineno}: expected {len(HEADER)} columns, got {len(row)}")
            symbol, date = row[0], row[1]
            parse_date(date, f"bars line {lineno} date")
            key = (symbol, date)
            if previous is not None and key <= previous:
                raise InputError(
                    f"bars line {lineno}: rows must be sorted by symbol then date, no duplicates"
                )
            previous = key
            o, h, lo, c = (_float(row[i], HEADER[i], lineno) for i in (2, 3, 4, 5))
            adj = _float(row[6], "adj_close", lineno) if row[6].strip() else None
            volume = _float(row[7], "volume", lineno) if row[7].strip() else 0.0
            if c <= 0 or (adj is not None and adj <= 0):
                raise InputError(f"bars line {lineno}: prices must be positive")
            out.setdefault(symbol, Series(symbol)).add(Bar(symbol, date, o, h, lo, c, adj, volume))
    return out


def write_bars(rows: Iterable[Bar], path: str | Path) -> None:
    """Canonical writer: floats as `.10g`, `\\n` endings, rows sorted by symbol then date."""
    lines = [",".join(HEADER)]
    for b in sorted(rows, key=lambda b: (b.symbol, b.date)):
        adj = "" if b.adj_close is None else num(b.adj_close)
        lines.append(
            ",".join(
                [b.symbol, b.date, num(b.open), num(b.high), num(b.low), num(b.close), adj, num(b.volume)]
            )
        )
    write_text(Path(path), "\n".join(lines) + "\n")
