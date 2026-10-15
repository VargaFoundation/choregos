"""Snapshot: tolerant response parsing, fake fetcher, exit code 3, real HTTP contract."""

from __future__ import annotations

import datetime as dt
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from choregos_quant.bars import read_bars
from choregos_quant.cli import main
from choregos_quant.errors import InputError
from choregos_quant.snapshot import Fetched, FetchError, HttpFetcher, parse_response

BAR = {"date": "2020-01-02", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "adj_close": None, "volume": 100}


def test_parse_response_both_shapes() -> None:
    a = parse_response({"result": {"provider": "p", "bars": [BAR]}}, "X")
    b = parse_response({"provider": "p", "bars": [BAR]}, "X")
    assert a == b == Fetched("p", [BAR])
    with pytest.raises(FetchError):
        parse_response({"result": {}}, "X")


def test_parse_response_reads_the_internal_api_answer() -> None:
    """The shape `POST /internal/runs/{id}/tools/{tool}` answers: the bars under `structuredContent`,
    a provider's refusal as a `status_code` in a 200 answer."""
    api = {"status_code": 200, "result": {"structuredContent": {"provider": "p", "bars": [BAR]}}}
    assert parse_response(api, "X") == Fetched("p", [BAR])
    with pytest.raises(FetchError, match="422"):
        parse_response({"status_code": 422, "result": {"error": "unknown symbol"}}, "X")


def fake(missing: set[str]) -> Any:
    def fetch(symbol: str, start: str, end: str) -> Fetched:
        if symbol in missing:
            raise FetchError("nope")
        return Fetched("fakeprov", [{**BAR, "date": "2020-01-03"}, BAR])

    return fetch


def args(work: Path, *extra: str) -> list[str]:
    return ["snapshot", "--campaign", str(work / "campaign.json"), "--out", str(work / "snap"), *extra]


def test_snapshot_ok(workdir: Path) -> None:
    assert main(args(workdir), fetcher=fake(set())) == 0
    snap = json.loads((workdir / "snap" / "snapshot.json").read_text())
    assert (
        snap["provider"] == "fakeprov"
        and snap["missing"] == []
        and snap["rows"] == {"AAA": 2, "BBB": 2, "CCC": 2}
    )
    assert snap["request"]["start"] == "2020-01-01" and snap["fetched_at"].endswith("Z")
    import hashlib

    assert snap["bars_sha256"] == hashlib.sha256((workdir / "snap" / "bars.csv").read_bytes()).hexdigest()
    assert read_bars(workdir / "snap" / "bars.csv")["AAA"].dates == ["2020-01-02", "2020-01-03"]


def test_snapshot_missing_symbols_exit_3(workdir: Path) -> None:
    assert main(args(workdir), fetcher=fake({"BBB"})) == 3
    assert json.loads((workdir / "snap" / "snapshot.json").read_text())["missing"] == ["BBB"]
    assert main(args(workdir, "--max-missing", "1"), fetcher=fake({"BBB"})) == 0


def test_empty_bars_count_as_missing(workdir: Path) -> None:
    def empty(symbol: str, start: str, end: str) -> Fetched:
        return Fetched("p", [] if symbol == "AAA" else [BAR])

    assert main(args(workdir), fetcher=empty) == 3


def test_http_fetcher_contract() -> None:
    seen: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            seen["path"] = self.path
            seen["auth"] = self.headers["Authorization"]
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            payload = json.dumps({"result": {"symbol": "X", "provider": "p", "bars": [BAR]}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {
            "CHOREGOS_API_URL": f"http://127.0.0.1:{server.server_port}/",
            "CHOREGOS_RUN_ID": "r1",
            "CHOREGOS_RUN_TOKEN": "tok",
        }
        got = HttpFetcher("marketdata__get_bars", env)("AAA", "2020-01-01", "2020-12-31")
    finally:
        server.shutdown()
    assert got.bars == [BAR]
    assert seen["path"] == "/runs/r1/tools/marketdata__get_bars"
    assert seen["auth"] == "Bearer tok"
    assert seen["body"] == {"symbol": "AAA", "start": "2020-01-01", "end": "2020-12-31"}


def test_http_fetcher_needs_its_environment() -> None:
    with pytest.raises(InputError, match="CHOREGOS_API_URL"):
        HttpFetcher("t", {})


def test_snapshot_is_deterministic_with_a_fixed_clock(workdir: Path) -> None:
    from choregos_quant.campaign import read_campaign
    from choregos_quant.snapshot import take_snapshot

    clock = lambda: dt.datetime(2026, 1, 1, tzinfo=dt.UTC)  # noqa: E731
    camp = read_campaign(workdir / "campaign.json")
    take_snapshot(camp, fake(set()), workdir / "s1", now=clock)
    take_snapshot(camp, fake(set()), workdir / "s2", now=clock)
    for n in ("bars.csv", "snapshot.json"):
        assert (workdir / "s1" / n).read_bytes() == (workdir / "s2" / n).read_bytes()
