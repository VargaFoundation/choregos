# SPDX-License-Identifier: Apache-2.0
"""Snapshot of market data through the platform's tool gateway (the only network access)."""

from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from choregos_quant.bars import Bar, write_bars
from choregos_quant.campaign import Campaign
from choregos_quant.canon import dumps, sha256_file, write_text
from choregos_quant.errors import InputError, SnapshotError


@dataclass(frozen=True)
class Fetched:
    provider: str
    bars: list[dict[str, Any]]


Fetcher = Callable[[str, str, str], Fetched]


class FetchError(RuntimeError):
    pass


def parse_response(payload: Any, symbol: str) -> Fetched:
    """The internal API answers `{status_code, result: {structuredContent: {bars, provider}}}`; a
    provider's refusal is a `status_code` other than 200 with a 200 HTTP answer. Also read: the bars
    at `result.bars` or `bars`."""
    if not isinstance(payload, dict):
        raise FetchError(f"{symbol}: response is not a JSON object")
    status = payload.get("status_code", 200)
    if status != 200:
        raise FetchError(f"{symbol}: the connector answered {status}")
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("structuredContent"), dict):
        result = result["structuredContent"]
    holder = result if isinstance(result, dict) else payload
    bars = holder.get("bars", payload.get("bars"))
    if not isinstance(bars, list):
        raise FetchError(f"{symbol}: no bars list in the response")
    provider = holder.get("provider") or payload.get("provider") or "unknown"
    return Fetched(str(provider), bars)


class HttpFetcher:
    """POST `{CHOREGOS_API_URL}/runs/{CHOREGOS_RUN_ID}/tools/{tool}` with the run token."""

    def __init__(
        self, tool: str = "marketdata__get_bars", env: dict[str, str] | None = None, timeout: float = 60
    ):
        e = dict(os.environ) if env is None else env
        try:
            self._url = f"{e['CHOREGOS_API_URL'].rstrip('/')}/runs/{e['CHOREGOS_RUN_ID']}/tools/{tool}"
            self._token = e["CHOREGOS_RUN_TOKEN"]
        except KeyError as exc:
            raise InputError(f"missing environment variable {exc.args[0]}") from exc
        self._timeout = timeout

    def __call__(self, symbol: str, start: str, end: str) -> Fetched:
        body = json.dumps({"symbol": symbol, "start": start, "end": end}).encode()
        request = urllib.request.Request(  # noqa: S310 - the URL comes from the run's own environment
            self._url,
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                return parse_response(json.loads(response.read()), symbol)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise FetchError(f"{symbol}: {exc}") from exc


def _opt(value: Any) -> float | None:
    return None if value is None or value == "" else float(value)


def _to_bar(symbol: str, raw: dict[str, Any]) -> Bar:
    return Bar(
        symbol,
        str(raw["date"]),
        float(raw["open"]),
        float(raw["high"]),
        float(raw["low"]),
        float(raw["close"]),
        _opt(raw.get("adj_close")),
        float(raw.get("volume") or 0),
    )


def take_snapshot(
    campaign: Campaign,
    fetch: Fetcher,
    out: Path,
    *,
    tool: str = "marketdata__get_bars",
    max_missing: int = 0,
    now: Callable[[], dt.datetime] | None = None,
) -> dict[str, Any]:
    """Write `bars.csv` and `snapshot.json`; raise `SnapshotError` when too many symbols are missing."""
    out.mkdir(parents=True, exist_ok=True)
    rows: list[Bar] = []
    counts: dict[str, int] = {}
    missing: list[str] = []
    providers: set[str] = set()
    for symbol in campaign.symbols:
        try:
            got = fetch(symbol, campaign.start, campaign.end)
            bars = [_to_bar(symbol, b) for b in got.bars]
        except (FetchError, KeyError, TypeError, ValueError):
            missing.append(symbol)
            continue
        if not bars:
            missing.append(symbol)
            continue
        providers.add(got.provider)
        by_date = {b.date: b for b in bars}
        rows.extend(by_date.values())
        counts[symbol] = len(by_date)
    write_bars(rows, out / "bars.csv")
    stamp = (now or (lambda: dt.datetime.now(dt.UTC)))().strftime("%Y-%m-%dT%H:%M:%SZ")
    snap = {
        "provider": ",".join(sorted(providers)),
        "fetched_at": stamp,
        "request": {"tool": tool, "symbols": campaign.symbols, "start": campaign.start, "end": campaign.end},
        "rows": {k: counts[k] for k in sorted(counts)},
        "missing": sorted(missing),
        "bars_sha256": sha256_file(out / "bars.csv"),
    }
    write_text(out / "snapshot.json", dumps(snap))
    facts = {
        "symbols": len(counts),
        "missing": len(missing),
        "data_rows": sum(counts.values()),
        "bars_sha256": snap["bars_sha256"],
    }
    write_text(out / "result.json", dumps({"facts": facts}))
    if len(missing) > max_missing:
        raise SnapshotError(f"{len(missing)} symbols missing (> {max_missing}): {', '.join(sorted(missing))}")
    return snap
