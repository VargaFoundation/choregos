# SPDX-License-Identifier: Apache-2.0
"""Snapshot of market data through the platform's tool gateway (the only network access).

A re-run of the step reads the snapshot an earlier run stored instead of the provider (S38-04): when
the stored `snapshot.json` answers the same request and `bars.csv` still hashes to its digest, both
are copied byte for byte and nothing is fetched. A free source that changed its history since, or
that is down today, cannot change a campaign already snapshotted. `--refresh` fetches anyway."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
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
    #: A `composite` source's comparison with a second source; `None` when nothing was compared.
    cross_check: dict[str, Any] | None = None


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
    check = holder.get("cross_check")
    return Fetched(str(provider), bars, check if isinstance(check, dict) else None)


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


def _request(campaign: Campaign, tool: str) -> dict[str, Any]:
    return {"tool": tool, "symbols": campaign.symbols, "start": campaign.start, "end": campaign.end}


def _facts(snap: dict[str, Any], reused: bool) -> dict[str, Any]:
    """`cross_checked`: symbols a second source confirmed on common days; `diverging`: symbols with
    at least one close beyond the composite's tolerance. A source that is down leaves a symbol
    unchecked, and `cross_checked < symbols` says so."""
    rows = dict(snap["rows"])
    checks = [c for c in (snap.get("cross_check") or {}).values() if isinstance(c, dict)]
    return {
        "symbols": len(rows),
        "missing": len(snap["missing"]),
        "data_rows": sum(rows.values()),
        "bars_sha256": snap["bars_sha256"],
        "reused": int(reused),
        "cross_checked": sum(1 for c in checks if c.get("common_days", 0) > 0),
        "diverging": sum(1 for c in checks if c.get("diverging_days", 0) > 0),
    }


def stored_snapshot(campaign: Campaign, previous: Path, tool: str) -> dict[str, Any] | None:
    """The snapshot an earlier run stored in `previous`, if it answers this request and its bars
    still hash to its digest; `None` otherwise — absent, another request, or bars that changed."""
    snap_path, bars_path = previous / "snapshot.json", previous / "bars.csv"
    if not (snap_path.is_file() and bars_path.is_file()):
        return None
    try:
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(snap, dict) or snap.get("request") != _request(campaign, tool):
        return None
    if snap.get("bars_sha256") != sha256_file(bars_path):
        return None
    return snap


def take_snapshot(
    campaign: Campaign,
    fetch: Fetcher,
    out: Path,
    *,
    tool: str = "marketdata__get_bars",
    max_missing: int = 0,
    now: Callable[[], dt.datetime] | None = None,
    previous: Path | None = None,
) -> dict[str, Any]:
    """Write `bars.csv` and `snapshot.json` — fetched, or copied from `previous` when it holds the
    same request (`stored_snapshot`); raise `SnapshotError` when too many symbols are missing."""
    out.mkdir(parents=True, exist_ok=True)
    stored = stored_snapshot(campaign, previous, tool) if previous is not None else None
    if previous is not None and stored is not None:
        shutil.copyfile(previous / "bars.csv", out / "bars.csv")
        write_text(out / "snapshot.json", dumps(stored))
        write_text(out / "result.json", dumps({"facts": _facts(stored, reused=True)}))
        if len(stored["missing"]) > max_missing:
            absent = len(stored["missing"])
            raise SnapshotError(f"{absent} symbols missing (> {max_missing}) in the stored snapshot")
        return stored
    rows: list[Bar] = []
    counts: dict[str, int] = {}
    checks: dict[str, dict[str, Any] | None] = {}
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
        checks[symbol] = got.cross_check
    write_bars(rows, out / "bars.csv")
    stamp = (now or (lambda: dt.datetime.now(dt.UTC)))().strftime("%Y-%m-%dT%H:%M:%SZ")
    snap = {
        "provider": ",".join(sorted(providers)),
        "fetched_at": stamp,
        "request": _request(campaign, tool),
        "rows": {k: counts[k] for k in sorted(counts)},
        "missing": sorted(missing),
        "cross_check": {k: checks[k] for k in sorted(checks)},
        "bars_sha256": sha256_file(out / "bars.csv"),
    }
    write_text(out / "snapshot.json", dumps(snap))
    write_text(out / "result.json", dumps({"facts": _facts(snap, reused=False)}))
    if len(missing) > max_missing:
        raise SnapshotError(f"{len(missing)} symbols missing (> {max_missing}): {', '.join(sorted(missing))}")
    return snap
