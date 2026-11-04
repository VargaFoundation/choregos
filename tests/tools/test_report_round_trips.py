"""The round-trip report (S57-01, ADR 0122): its aggregation on fixtures, and its walk of the API.

The operator runs it on production; these tests hold what it computes from what the API serves, and
that its `--sql` is the plan's §4.3, so the two ways of taking the baseline say the same thing.
"""

from __future__ import annotations

import json
import pathlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from tools.report_round_trips import (
    MEASURES,
    RunSample,
    aggregate,
    in_window,
    main,
    percentile,
    platform_tool_calls,
    render,
    report,
    sample,
    sql,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOW = datetime.now(UTC).replace(microsecond=0)


def _at(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _run(run_id: str, **fields: Any) -> dict[str, Any]:
    """A run as `GET /work-items/{id}/runs` serves it."""
    record: dict[str, Any] = {
        "id": run_id,
        "status": "succeeded",
        "stage_role": "implement",
        "backend": "claude-code",
        "agent_slug": None,
        "cost_usd": 0.5,
        "started_at": _at(2),
        "ended_at": _at(2 - 1 / 24),
        "tokens": {"tokens_in": 10_000, "tokens_cached": 4_000, "tokens_out": 800, "runs": 1, "requests": 6},
        "result": {"diagnostics": {"turns": 9, "tool_calls": 14, "duration_s": 300.0}},
    }
    record.update(fields)
    return record


def _access(tool_calls: dict[str, int]) -> dict[str, Any]:
    """`GET /runs/{id}/access`: the platform's tools as `tool` lines, beside the run's other accesses."""
    lines = [{"nature": "tool", "target": t, "requests": n, "refusals": 0} for t, n in tool_calls.items()]
    lines.append({"nature": "network", "target": "pypi.org", "requests": 40, "refusals": 0})
    lines.append({"nature": "read", "target": "src/app.py", "requests": 3, "refusals": 0})
    return {"events": 60, "access": lines, "tool_cost_eur": 0.0}


# ───────────────────────────── one run ─────────────────────────────


def test_percentile_is_postgresql_s_percentile_cont() -> None:
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([1, 2, 3, 4], 0.9) == pytest.approx(3.7)
    assert percentile([4, 1, 3, 2], 0.9) == pytest.approx(3.7), "order does not matter"
    assert percentile([7], 0.9) == 7
    assert percentile([], 0.5) is None


def test_platform_calls_are_the_tool_lines_of_the_access_report_only() -> None:
    assert platform_tool_calls(_access({"jira_search": 3, "web_fetch": 2})) == 5
    assert platform_tool_calls(_access({})) == 0
    assert platform_tool_calls(None) is None, "an unreadable report is unknown, not zero"


def test_a_run_s_measures_come_from_its_dto_and_its_access_report() -> None:
    one = sample(_run("r1", agent_slug="fon-agent"), _access({"jira_search": 4}))
    assert one == RunSample(
        agent="fon-agent",
        backend="claude-code",
        succeeded=True,
        requests=6,
        platform_tool_calls=4,
        agent_tool_calls=14,
        turns=9,
        tokens_in=10_000,
        tokens_cached=4_000,
        tokens_out=800,
        cost_usd=0.5,
        wall_time_s=300.0,
    )


def test_a_run_without_an_agent_is_named_by_its_stage() -> None:
    assert sample(_run("r1"), None).agent == "implement"


def test_a_run_booked_before_the_count_has_no_requests() -> None:
    tokens = {"tokens_in": 10, "tokens_cached": 0, "tokens_out": 1, "runs": 1, "requests": None}
    assert sample(_run("r1", tokens=tokens), None).requests is None


def test_a_run_never_booked_has_unknown_tokens_not_zeros() -> None:
    unbooked = {"tokens_in": 0, "tokens_cached": 0, "tokens_out": 0, "runs": 0, "requests": None}
    one = sample(_run("r1", tokens=unbooked, result=None), None)
    assert (one.tokens_in, one.tokens_cached, one.tokens_out) == (None, None, None)
    assert (one.agent_tool_calls, one.turns) == (None, None), "no result, no diagnostics"


def test_the_wall_time_falls_back_on_the_run_s_timestamps() -> None:
    one = sample(_run("r1", result={"diagnostics": {"duration_s": 0.0}}), None)
    assert one.wall_time_s == pytest.approx(3600.0)


def test_the_window_is_started_after_since_and_ended() -> None:
    since = NOW - timedelta(days=30)
    assert in_window(_run("r1"), since)
    assert not in_window(_run("r1", started_at=_at(31)), since)
    assert not in_window(_run("r1", ended_at=None), since), "a run still going is not counted yet"
    assert not in_window(_run("r1", started_at=None), since)


# ───────────────────────────── the report ─────────────────────────────


def test_rows_are_per_agent_and_backend_with_p50_p90_and_the_share_of_busy_runs() -> None:
    samples = [
        RunSample("fon-agent", "claude-code", True, requests=r, platform_tool_calls=c, cost_usd=0.1)
        for r, c in [(4, 0), (6, 5), (10, 7), (None, 2)]
    ]
    samples += [
        RunSample("fon-agent", "codex", False, requests=3, platform_tool_calls=None, cost_usd=1.0),
        RunSample("review", "claude-code", False, requests=2, platform_tool_calls=1, cost_usd=0.2),
        RunSample("review", "claude-code", True, requests=8, platform_tool_calls=9, cost_usd=0.3),
    ]
    rows = aggregate(samples)
    assert [(r.agent, r.backend, r.runs) for r in rows] == [
        ("fon-agent", "claude-code", 4),
        ("review", "claude-code", 2),
        ("fon-agent", "codex", 1),
    ], "the busiest first"
    busiest = rows[0]
    assert busiest.success_rate == 1.0
    # A run booked before S57-01 is left out of the percentile: 4, 6, 10, not 0, 4, 6, 10.
    assert (busiest.spreads["requests"].p50, busiest.spreads["requests"].p90) == (6, pytest.approx(9.2))
    assert busiest.share_many_platform_calls == 0.5, "two of four runs made 5 calls or more"
    assert busiest.cost_usd_total == pytest.approx(0.4)
    assert rows[1].success_rate == 0.5
    codex = rows[2]
    assert codex.share_many_platform_calls is None, "nobody read its calls: unknown, not 0 %"
    assert codex.spreads["platform_tool_calls"].p50 is None


def test_the_rows_render_as_a_table_and_as_json() -> None:
    rows = aggregate([RunSample("fon-agent", "", True, requests=3, platform_tool_calls=6, cost_usd=0.25)])
    table = render(rows).splitlines()
    assert table[0].split()[:4] == ["agent", "backend", "runs", "ok"]
    assert table[1].split()[:6] == ["fon-agent", "-", "1", "1.00", "3/3", "6/6"]
    record = rows[0].to_dict()
    assert record["requests_p90"] == 3 and record["share_5_platform_calls_or_more"] == 1.0
    assert render([]) == "no ended run in the window"


def test_the_sql_is_the_plan_s_baseline_query() -> None:
    """`--sql` and the plan's §4.3 are one text: the operator gets what the plan says it gets."""
    plan = (ROOT / "docs" / "plan" / "PLAN-2026-11-03-tool-scripts.md").read_text(encoding="utf-8")
    section = plan.split("### 4.3 ", 1)[1].split("### 4.4 ", 1)[0]
    block = re.search(r"```sql\n(.*?)```", section, re.DOTALL)
    assert block is not None
    assert block.group(1) == sql(30)
    assert "interval '7 days'" in sql(7)
    with pytest.raises(ValueError, match="at least one day"):
        sql(0)


def test_the_sql_computes_every_column_the_tool_prints() -> None:
    query = sql(30)
    for name, _ in MEASURES:
        assert f"as {name}_p50" in query and f"as {name}_p90" in query, name
    assert "as cost_usd_total" in query and "as share_5_platform_calls_or_more" in query


# ───────────────────────────── the walk of the API ─────────────────────────────


class FakeApi:
    """The public routes the report reads, two projects deep, paged by 1, with one refusal of each kind."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.tokens: set[str] = set()
        self.projects = [{"id": "p1"}, {"id": "p2"}]
        self.items = {
            "p1": [
                {"id": "w1", "created_at": _at(5), "closed_at": None},
                # Closed before the window: its runs are never asked for.
                {"id": "w-old", "created_at": _at(90), "closed_at": _at(60)},
                # A list entry the token may not open (ADR 0114): skipped, and counted.
                {"id": "w-marked", "created_at": _at(3), "closed_at": None},
            ],
            "p2": [{"id": "w2", "created_at": _at(40), "closed_at": _at(1)}],
        }
        self.trials = {"p1": [], "p2": [{"id": "w-trial", "created_at": _at(2), "closed_at": None}]}
        self.runs = {
            "w1": [
                _run("run-1", agent_slug="fon-agent"),
                _run("run-2", agent_slug="fon-agent", status="failed"),
                _run("run-older", agent_slug="fon-agent", started_at=_at(45), ended_at=_at(45)),
                _run("run-going", agent_slug="fon-agent", ended_at=None),
            ],
            "w2": [_run("run-3", agent_slug="fon-agent", backend="codex")],
            "w-trial": [_run("run-trial", agent_slug="fon-agent")],
        }
        self.access = {
            "run-1": _access({"jira_search": 5, "jira_get": 1}),
            "run-2": _access({"jira_search": 1}),
            "run-trial": _access({}),
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.tokens.add(request.headers.get("authorization", ""))
        path = request.url.path.removeprefix("/api/v1")
        self.seen.append(path)
        if path == "/orgs/acme/projects":
            return self._page(self.projects, request)
        if match := re.fullmatch(r"/projects/(\w+)/work-items", path):
            listing = self.trials if request.url.params.get("state") == "trial" else self.items
            return self._page(listing[match.group(1)], request)
        if match := re.fullmatch(r"/work-items/([\w-]+)/runs", path):
            runs = self.runs.get(match.group(1))
            return httpx.Response(200, json=runs) if runs is not None else httpx.Response(404, json={})
        if match := re.fullmatch(r"/runs/([\w-]+)/access", path):
            access = self.access.get(match.group(1))
            return httpx.Response(200, json=access) if access is not None else httpx.Response(403, json={})
        return httpx.Response(500, json={"path": path})

    @staticmethod
    def _page(items: list[dict[str, Any]], request: httpx.Request) -> httpx.Response:
        """One item per page: the walk must follow `meta.next_cursor`."""
        ids = [item["id"] for item in items]
        cursor = request.url.params.get("cursor")
        start = ids.index(cursor) + 1 if cursor else 0
        window = items[start : start + 1]
        more = start + 1 < len(items)
        meta = {"next_cursor": window[-1]["id"] if window and more else None, "has_more": more}
        return httpx.Response(200, json={"items": window, "meta": meta})


async def test_the_walk_reads_every_project_page_and_keeps_the_window() -> None:
    api = FakeApi()
    rows, found = await report(
        "https://choregos.test", "acme", "op-token", 30, now=NOW, transport=httpx.MockTransport(api.handler)
    )
    assert "/work-items/w-old/runs" not in api.seen, "a work item closed before the window is not opened"
    assert "/runs/run-older/access" not in api.seen and "/runs/run-going/access" not in api.seen
    assert (found.skipped_work_items, found.skipped_access) == (1, 1)
    by_backend = {row.backend: row for row in rows}
    claude = by_backend["claude-code"]
    assert claude.runs == 3, "run-1, run-2 and the trial's run"
    assert claude.success_rate == pytest.approx(0.6667)
    assert claude.spreads["platform_tool_calls"].p50 == 1
    assert claude.share_many_platform_calls == pytest.approx(0.3333)
    codex = by_backend["codex"]
    assert codex.runs == 1 and codex.spreads["platform_tool_calls"].p50 is None
    assert api.tokens == {"Bearer op-token"}


def test_the_command_prints_the_table_and_never_the_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = FakeApi()
    monkeypatch.setenv("ROUND_TRIPS_TOKEN", "op-secret-token")
    code = main(
        ["--api", "https://choregos.test", "--org", "acme", "--token-env", "ROUND_TRIPS_TOKEN", "--json"],
        transport=httpx.MockTransport(api.handler),
    )
    out = capsys.readouterr()
    assert code == 0
    rows = json.loads(out.out)
    assert {row["backend"] for row in rows} == {"claude-code", "codex"}
    assert "skipped: 1 work items" in out.err
    assert "op-secret-token" not in out.out + out.err


def test_the_command_without_a_token_refuses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CHOREGOS_TOKEN", raising=False)
    assert main(["--api", "https://choregos.test", "--org", "acme"]) == 2
    assert "no token: set CHOREGOS_TOKEN" in capsys.readouterr().err


def test_the_sql_mode_needs_no_api_and_no_token(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--sql", "--days", "14"]) == 0
    assert "interval '14 days'" in capsys.readouterr().out
