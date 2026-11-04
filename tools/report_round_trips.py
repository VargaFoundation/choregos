# SPDX-License-Identifier: Apache-2.0
"""The round trips of an organisation's runs, per agent and backend (S57-01, ADR 0122).

    CHOREGOS_TOKEN=... uv run python tools/report_round_trips.py \\
        --api https://choregos.example --org acme --days 30

    uv run python tools/report_round_trips.py --sql --days 30

For each agent (the registry agent, else the stage role) and backend, over the runs that started in
the window and have ended: the runs, the success rate (`succeeded` over ended runs), the p50 and p90
of the model requests (`tokens.requests`, null for runs booked before S57-01), the platform tool calls
(the ledger's `kind="tool"` rows, from `GET /runs/{id}/access`), the agent's own tool calls
(`result.diagnostics.tool_calls`) and turns, `tokens_in`, `tokens_cached`, `tokens_out`, the cost
and the wall time; then the total cost, and the share of runs with at least 5 platform tool calls. A
value nobody measured is left out of its percentile, never counted as zero.

It reads through the public API only, with an operator's token taken from the variable named by
`--token-env` (`CHOREGOS_TOKEN` by default) and never printed. No route lists an organisation's runs
over a window, so it walks `GET /orgs/{org}/projects`, each project's `GET /projects/{id}/work-items`
(trials included; a work item closed before the window is not opened), `GET /work-items/{id}/runs`
and, for each run in the window, `GET /runs/{id}/access`. A work item or a run the token cannot read
is skipped and counted on standard error. `--sql` prints the same report as one read-only query
(the plan's §4.3, `docs/plan/PLAN-2026-11-03-tool-scripts.md`) for an operator with database access.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

API = "/api/v1"
#: The platform tool calls from which a run counts as one a script could have chained.
MANY_CALLS = 5
#: The measures of a run, in the order they are printed, with the decimals they are printed with.
MEASURES: tuple[tuple[str, int], ...] = (
    ("requests", 0),
    ("platform_tool_calls", 0),
    ("agent_tool_calls", 0),
    ("turns", 0),
    ("tokens_in", 0),
    ("tokens_cached", 0),
    ("tokens_out", 0),
    ("cost_usd", 4),
    ("wall_time_s", 1),
)

#: The plan's §4.3 query; `__DAYS__` is the window. A test keeps it equal to the plan's text.
SQL_TEMPLATE = """\
with r as (
  select r.id, coalesce(r.agent_slug, r.stage_role) as agent, r.backend, r.status,
         nullif(r.tokens->>'requests','')::numeric                  as requests,
         (select count(*) from cost_ledger l
           where l.run_id = r.id and l.kind = 'tool')               as platform_tool_calls,
         nullif(r.result->'diagnostics'->>'tool_calls','')::numeric as agent_tool_calls,
         nullif(r.result->'diagnostics'->>'turns','')::numeric      as turns,
         nullif(r.tokens->>'tokens_in','')::numeric                 as tokens_in,
         nullif(r.tokens->>'tokens_cached','')::numeric             as tokens_cached,
         nullif(r.tokens->>'tokens_out','')::numeric                as tokens_out,
         r.cost_usd,
         coalesce(nullif(nullif(r.result->'diagnostics'->>'duration_s','')::numeric, 0),
                  extract(epoch from (r.ended_at - r.started_at)))  as wall_time_s
  from runs r
  where r.started_at > now() - interval '__DAYS__ days' and r.ended_at is not null
)
select agent, backend, count(*) as runs,
       round(avg(case when status = 'succeeded' then 1 else 0 end), 4)     as success_rate,
       percentile_cont(0.5) within group (order by requests)               as requests_p50,
       percentile_cont(0.9) within group (order by requests)               as requests_p90,
       percentile_cont(0.5) within group (order by platform_tool_calls)    as platform_tool_calls_p50,
       percentile_cont(0.9) within group (order by platform_tool_calls)    as platform_tool_calls_p90,
       percentile_cont(0.5) within group (order by agent_tool_calls)       as agent_tool_calls_p50,
       percentile_cont(0.9) within group (order by agent_tool_calls)       as agent_tool_calls_p90,
       percentile_cont(0.5) within group (order by turns)                  as turns_p50,
       percentile_cont(0.9) within group (order by turns)                  as turns_p90,
       percentile_cont(0.5) within group (order by tokens_in)              as tokens_in_p50,
       percentile_cont(0.9) within group (order by tokens_in)              as tokens_in_p90,
       percentile_cont(0.5) within group (order by tokens_cached)          as tokens_cached_p50,
       percentile_cont(0.9) within group (order by tokens_cached)          as tokens_cached_p90,
       percentile_cont(0.5) within group (order by tokens_out)             as tokens_out_p50,
       percentile_cont(0.9) within group (order by tokens_out)             as tokens_out_p90,
       percentile_cont(0.5) within group (order by cost_usd)               as cost_usd_p50,
       percentile_cont(0.9) within group (order by cost_usd)               as cost_usd_p90,
       percentile_cont(0.5) within group (order by wall_time_s)            as wall_time_s_p50,
       percentile_cont(0.9) within group (order by wall_time_s)            as wall_time_s_p90,
       round(sum(cost_usd)::numeric, 4)                                    as cost_usd_total,
       round(avg(case when platform_tool_calls >= 5 then 1 else 0 end), 4) as share_5_platform_calls_or_more
from r group by 1, 2 order by runs desc;
"""


def sql(days: int) -> str:
    """The report as one read-only query over the last `days` days."""
    if days < 1:
        raise ValueError("the window is at least one day")
    return SQL_TEMPLATE.replace("__DAYS__", str(int(days)))


# ───────────────────────────────────────── one run ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RunSample:
    """What one run measured; `None` is "nobody measured it", never zero."""

    agent: str
    backend: str
    succeeded: bool
    requests: int | None = None
    platform_tool_calls: int | None = None
    agent_tool_calls: int | None = None
    turns: int | None = None
    tokens_in: int | None = None
    tokens_cached: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    wall_time_s: float | None = None

    def measure(self, name: str) -> float | None:
        value = getattr(self, name)
        return None if value is None else float(value)


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    instant = datetime.fromisoformat(value)
    return instant if instant.tzinfo is not None else instant.replace(tzinfo=UTC)


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def platform_tool_calls(access: Mapping[str, Any] | None) -> int | None:
    """The run's calls that reached `callRunTool` and were booked: the `tool` lines of its access
    report, which the API folds from the cost ledger's `kind="tool"` rows."""
    if access is None:
        return None
    lines = access.get("access") or []
    return sum(int(line.get("requests", 0)) for line in lines if line.get("nature") == "tool")


def sample(run: Mapping[str, Any], access: Mapping[str, Any] | None) -> RunSample:
    """A run as `GET /work-items/{id}/runs` serves it, and its access report (`None`: unreadable)."""
    tokens: Mapping[str, Any] = run.get("tokens") or {}
    # `runs` is 1 once the spend was booked: before that the token counts are not zeros, they are unknown.
    booked = bool(tokens.get("runs"))
    diagnostics: Mapping[str, Any] = (run.get("result") or {}).get("diagnostics") or {}
    started, ended = _instant(run.get("started_at")), _instant(run.get("ended_at"))
    duration = diagnostics.get("duration_s")
    wall: float | None = float(duration) if isinstance(duration, int | float) and duration > 0 else None
    if wall is None and started is not None and ended is not None:
        wall = (ended - started).total_seconds()
    return RunSample(
        agent=str(run.get("agent_slug") or run.get("stage_role") or ""),
        backend=str(run.get("backend") or ""),
        succeeded=run.get("status") == "succeeded",
        requests=_int(tokens.get("requests")),
        platform_tool_calls=platform_tool_calls(access),
        agent_tool_calls=_int(diagnostics.get("tool_calls")) if diagnostics else None,
        turns=_int(diagnostics.get("turns")) if diagnostics else None,
        tokens_in=_int(tokens.get("tokens_in")) if booked else None,
        tokens_cached=_int(tokens.get("tokens_cached")) if booked else None,
        tokens_out=_int(tokens.get("tokens_out")) if booked else None,
        cost_usd=float(run.get("cost_usd") or 0.0),
        wall_time_s=wall,
    )


def in_window(run: Mapping[str, Any], since: datetime) -> bool:
    """The query's window: started after `since`, and ended."""
    started = _instant(run.get("started_at"))
    return started is not None and started > since and _instant(run.get("ended_at")) is not None


# ───────────────────────────────────────── the report ─────────────────────────────────────────


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """PostgreSQL's `percentile_cont`: linear interpolation between the closest ranks."""
    if not values:
        return None
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


@dataclass(frozen=True, slots=True)
class Spread:
    p50: float | None
    p90: float | None


@dataclass(frozen=True, slots=True)
class Row:
    agent: str
    backend: str
    runs: int
    success_rate: float
    spreads: dict[str, Spread] = field(default_factory=dict)
    cost_usd_total: float = 0.0
    #: Over the runs whose platform calls were read; `None` when none was.
    share_many_platform_calls: float | None = None

    def to_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "agent": self.agent,
            "backend": self.backend,
            "runs": self.runs,
            "success_rate": self.success_rate,
        }
        for name, spread in self.spreads.items():
            record[f"{name}_p50"], record[f"{name}_p90"] = spread.p50, spread.p90
        record["cost_usd_total"] = self.cost_usd_total
        record[f"share_{MANY_CALLS}_platform_calls_or_more"] = self.share_many_platform_calls
        return record


def aggregate(samples: Iterable[RunSample]) -> list[Row]:
    """One row per agent and backend, the busiest first."""
    groups: dict[tuple[str, str], list[RunSample]] = {}
    for one in samples:
        groups.setdefault((one.agent, one.backend), []).append(one)
    rows: list[Row] = []
    for (agent, backend), runs in groups.items():
        spreads: dict[str, Spread] = {}
        for name, _ in MEASURES:
            values = [v for v in (r.measure(name) for r in runs) if v is not None]
            spreads[name] = Spread(percentile(values, 0.5), percentile(values, 0.9))
        read = [r.platform_tool_calls for r in runs if r.platform_tool_calls is not None]
        rows.append(
            Row(
                agent=agent,
                backend=backend,
                runs=len(runs),
                success_rate=round(sum(1 for r in runs if r.succeeded) / len(runs), 4),
                spreads=spreads,
                cost_usd_total=round(sum(r.cost_usd or 0.0 for r in runs), 4),
                share_many_platform_calls=(
                    round(sum(1 for n in read if n >= MANY_CALLS) / len(read), 4) if read else None
                ),
            )
        )
    return sorted(rows, key=lambda row: (-row.runs, row.agent, row.backend))


def _number(value: float | None, decimals: int) -> str:
    return "-" if value is None else f"{value:.{decimals}f}"


def render(rows: Sequence[Row]) -> str:
    """A fixed-width table: one line per agent and backend, `p50/p90` for each measure."""
    header = ["agent", "backend", "runs", "ok", *[f"{name} p50/p90" for name, _ in MEASURES]]
    header += ["cost_usd_total", f"share>={MANY_CALLS}"]
    lines = [header]
    for row in rows:
        cells = [row.agent or "-", row.backend or "-", str(row.runs), f"{row.success_rate:.2f}"]
        for name, decimals in MEASURES:
            spread = row.spreads[name]
            cells.append(f"{_number(spread.p50, decimals)}/{_number(spread.p90, decimals)}")
        cells += [f"{row.cost_usd_total:.4f}", _number(row.share_many_platform_calls, 2)]
        lines.append(cells)
    if len(lines) == 1:
        return "no ended run in the window"
    widths = [max(len(line[i]) for line in lines) for i in range(len(header))]
    return "\n".join("  ".join(cell.ljust(w) for cell, w in zip(line, widths, strict=True)) for line in lines)


# ───────────────────────────────────────── reading the API ─────────────────────────────────────────


@dataclass(slots=True)
class Collected:
    samples: list[RunSample] = field(default_factory=list)
    #: What the token could not read: work items whose runs, runs whose access report.
    skipped_work_items: int = 0
    skipped_access: int = 0


async def _pages(http: httpx.AsyncClient, path: str, params: Mapping[str, str]) -> list[dict[str, Any]]:
    """Every item of a paged listing (`items`, `meta.next_cursor`). A refusal raises."""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        query = {**params, "limit": "200", **({"cursor": cursor} if cursor else {})}
        response = await http.get(f"{API}{path}", params=query)
        response.raise_for_status()
        page = response.json()
        items += list(page.get("items") or [])
        cursor = (page.get("meta") or {}).get("next_cursor")
        if not cursor:
            return items


async def collect(http: httpx.AsyncClient, org: str, since: datetime, *, concurrency: int = 8) -> Collected:
    """The samples of the organisation's runs that started after `since` and have ended."""
    gate = asyncio.Semaphore(concurrency)
    found = Collected()

    async def read(path: str) -> Any | None:
        async with gate:
            response = await http.get(f"{API}{path}")
        return response.json() if response.is_success else None

    async def one_run(run: dict[str, Any]) -> None:
        access = await read(f"/runs/{run['id']}/access")
        if access is None:
            found.skipped_access += 1
        found.samples.append(sample(run, access))

    async def one_item(item: dict[str, Any]) -> None:
        closed = _instant(item.get("closed_at"))
        if closed is not None and closed <= since:
            return  # closed before the window: no run of it can start inside
        runs = await read(f"/work-items/{item['id']}/runs")
        if runs is None:
            found.skipped_work_items += 1
            return
        await asyncio.gather(*(one_run(run) for run in runs if in_window(run, since)))

    for project in await _pages(http, f"/orgs/{org}/projects", {}):
        # A trial's work item (S32-03) is listed only when asked for by its state.
        items = await _pages(http, f"/projects/{project['id']}/work-items", {})
        items += await _pages(http, f"/projects/{project['id']}/work-items", {"state": "trial"})
        await asyncio.gather(*(one_item(item) for item in items))
    return found


# ───────────────────────────────────────── the command ─────────────────────────────────────────


async def report(
    api: str,
    org: str,
    token: str,
    days: int,
    *,
    now: datetime | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[list[Row], Collected]:
    if not token.isprintable():
        # No value in the message: HTTP libraries echo an illegal header value, token included.
        raise ValueError("the token holds a control character")
    since = (now or datetime.now(UTC)) - timedelta(days=days)
    async with httpx.AsyncClient(
        base_url=api, headers={"Authorization": f"Bearer {token}"}, timeout=60, transport=transport
    ) as http:
        found = await collect(http, org, since)
    return aggregate(found.samples), found


def main(argv: list[str] | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api", help="console/API base URL, e.g. https://choregos.example")
    parser.add_argument("--org", help="the organisation's slug")
    parser.add_argument("--days", type=int, default=30, help="the window, in days (default 30)")
    parser.add_argument("--token-env", default="CHOREGOS_TOKEN", help="the variable holding the token")
    parser.add_argument("--json", action="store_true", help="print the rows as JSON")
    parser.add_argument("--sql", action="store_true", help="print the report as one SQL query")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days is at least 1")
    if args.sql:
        print(sql(args.days), end="")
        return 0
    if not args.api or not args.org:
        parser.error("--api and --org are required, unless --sql")
    token = os.environ.get(args.token_env, "")
    if not token:
        print(f"no token: set {args.token_env}", file=sys.stderr)
        return 2
    rows, found = asyncio.run(report(args.api, args.org, token, args.days, transport=transport))
    print(json.dumps([row.to_dict() for row in rows], indent=1) if args.json else render(rows))
    if found.skipped_work_items or found.skipped_access:
        print(
            f"skipped: {found.skipped_work_items} work items whose runs, "
            f"{found.skipped_access} runs whose access report the token could not read",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
