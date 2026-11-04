# SPDX-License-Identifier: Apache-2.0
"""The platform's Prometheus metrics: computed from the database, exposed by the API.

WHY FROM THE DATABASE
---------------------
The chart shipped a ServiceMonitor, four alerts and six dashboards that read sixteen `choregos_*`
series that no process emitted (inventory of 2026-09-24). The classic way, in-memory counters in
every process, would have given per-replica numbers to stitch together, and would have required an
HTTP server in the orchestrator. Here, everything that counts (runs, costs, tickets, refusals) is
ALREADY in the database: one truth, the same on every API replica, refreshed every few seconds and
served on `/metrics`. Only `http_requests_total` is a real process counter.

WHAT IT DOES NOT DO
-------------------
No traces. This module gives the series that the dashboards read; the inventory `NAMES` is what a
test compares with the dashboards and the alerts, so that a panel never again cites a metric that
does not exist.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from choregos_core import utcnow
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client import Counter as PromCounter
from prometheus_client.core import GaugeMetricFamily, HistogramMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy import func, select

from . import __version__
from .edition import current as current_edition
from .logging import get_logger

logger = get_logger("choregos.metrics")

#: The inventory: what `/metrics` can produce. The dashboards test refers to it.
NAMES = frozenset(
    {
        "http_requests_total",
        "choregos_runs_active",
        "choregos_runs_total",
        "choregos_run_start_seconds",
        "choregos_cost_usd_total",
        "choregos_project_daily_cost_usd",
        "choregos_project_daily_budget_usd",
        "choregos_first_pass_merge_rate",
        "choregos_releases_total",
        "choregos_lead_time_seconds",
        "choregos_train_frozen",
        "choregos_train_status",
        "choregos_permission_denials_total",
        "choregos_memory_pending",
        "choregos_context_pack_empty_total",
        "choregos_edition_info",
    }
)

START_SECONDS = (5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0)
DELIVERY_SECONDS = (3600.0, 4 * 3600.0, 8 * 3600.0, 24 * 3600.0, 3 * 86400.0, 7 * 86400.0, 30 * 86400.0)
ENVIRONMENTS = ("dev", "staging", "prod")
#: The dashboards show the state of a train as a number.
#: The query the `ReleaseTrain` workflow answers (`workflows/train.py`, `status_query`), as the
#: trains route asks it.
TRAIN_STATUS_QUERY = "status_query"
#: Keyed by the `status` the train's `status_query` returns (`ReleaseStatus`). "staged" was never a
#: status: a train at staging fell to -1 (S26-03).
TRAIN_STATES = {
    "collecting": 0,
    "departing": 1,
    "staging": 2,
    "awaiting_approval": 3,
    "promoting": 4,
    "verifying": 5,
}

registry = CollectorRegistry()
http_requests = PromCounter(
    "http_requests_total",
    "HTTP requests received by the API",
    ["method", "route", "status"],
    registry=registry,
)


def _buckets(values: Iterable[float], bounds: tuple[float, ...]) -> tuple[list[tuple[str, float]], float]:
    """Cumulative counts per bound, as Prometheus expects them, and the sum."""
    items = list(values)
    cumulative = [(str(b), float(sum(1 for v in items if v <= b))) for b in bounds]
    cumulative.append(("+Inf", float(len(items))))
    return cumulative, float(sum(items))


class Snapshot:
    """What the database said at the last refresh. Empty until one has happened."""

    def __init__(self) -> None:
        self.runs_active = 0.0
        self.runs_total: dict[str, float] = {}
        self.run_start: tuple[list[tuple[str, float]], float] = ([], 0.0)
        self.cost: dict[tuple[str, str, str], float] = {}
        self.daily_cost: dict[str, float] = {}
        self.daily_budget: dict[str, float] = {}
        self.first_pass: dict[str, float] = {}
        self.releases: dict[str, float] = {}
        self.lead_time: tuple[list[tuple[str, float]], float] = ([], 0.0)
        self.train_frozen: dict[tuple[str, str], float] = {}
        self.train_status: dict[tuple[str, str], float] = {}
        self.denials: dict[str, float] = {}
        self.memory_pending = 0.0
        self.context_pack_empty: dict[str, float] = {}
        self.refreshed_at: float | None = None


class _Current:
    """The latest snapshot. A holder rather than a `global`: it is replaced in one piece."""

    snapshot = Snapshot()


class PlatformCollector(Collector):
    """Renders the snapshot as series. No side effect, no database: reading is instantaneous."""

    def collect(self) -> Iterable[Any]:
        s = _Current.snapshot
        # The edition does not come from the database: it is a property of the process. An `_info`
        # series at 1 with the edition as a label is the usual form: it joins the others, so a
        # dashboard can say "which edition produced this figure". Without it, the only way to know
        # what an operator is connected to is to call `/edition` by hand, and nothing says it in
        # the history of the metrics.
        edition = GaugeMetricFamily(
            "choregos_edition_info", "Edition that is running (ADR 0024)", labels=["edition", "version"]
        )
        edition.add_metric([current_edition(), __version__], 1.0)
        yield edition
        yield GaugeMetricFamily("choregos_runs_active", "Runs queued or running", value=s.runs_active)
        g = GaugeMetricFamily("choregos_runs_total", "Runs by final state", labels=["status"])
        for status, n in s.runs_total.items():
            g.add_metric([status], n)
        yield g
        yield HistogramMetricFamily(
            "choregos_run_start_seconds",
            "From the creation of a run to its start (last 30 days)",
            buckets=s.run_start[0],
            sum_value=s.run_start[1],
        )
        g = GaugeMetricFamily(
            "choregos_cost_usd_total", "Spend in the ledger, USD", labels=["project", "stage", "model"]
        )
        for (project, stage, model), v in s.cost.items():
            g.add_metric([project, stage, model], v)
        yield g
        g = GaugeMetricFamily(
            "choregos_project_daily_cost_usd", "Spend of the day (UTC) per project", labels=["project"]
        )
        for project, v in s.daily_cost.items():
            g.add_metric([project], v)
        yield g
        g = GaugeMetricFamily(
            "choregos_project_daily_budget_usd", "Daily budget of the policy", labels=["project"]
        )
        for project, v in s.daily_budget.items():
            g.add_metric([project], v)
        yield g
        g = GaugeMetricFamily(
            "choregos_first_pass_merge_rate", "Share of PRs merged on the first pass", labels=["project"]
        )
        for project, v in s.first_pass.items():
            g.add_metric([project], v)
        yield g
        g = GaugeMetricFamily("choregos_releases_total", "Releases by state", labels=["status"])
        for status, n in s.releases.items():
            g.add_metric([status], n)
        yield g
        yield HistogramMetricFamily(
            "choregos_lead_time_seconds",
            "From the creation of a ticket to its closing (last 90 days)",
            buckets=s.lead_time[0],
            sum_value=s.lead_time[1],
        )
        g = GaugeMetricFamily("choregos_train_frozen", "1 if the train is frozen", labels=["project", "env"])
        for (project, env), v in s.train_frozen.items():
            g.add_metric([project, env], v)
        yield g
        g = GaugeMetricFamily(
            "choregos_train_status",
            "State of the train (0 collecting ... 5 verifying)",
            labels=["project", "env"],
        )
        for (project, env), v in s.train_status.items():
            g.add_metric([project, env], v)
        yield g
        g = GaugeMetricFamily(
            "choregos_permission_denials_total", "Permission requests refused", labels=["project"]
        )
        for project, v in s.denials.items():
            g.add_metric([project], v)
        yield g
        yield GaugeMetricFamily(
            "choregos_memory_pending", "Memory facts awaiting validation", value=s.memory_pending
        )
        g = GaugeMetricFamily(
            "choregos_context_pack_empty_total", "Runs started with an empty context pack", labels=["project"]
        )
        for project, v in s.context_pack_empty.items():
            g.add_metric([project], v)
        yield g


registry.register(PlatformCollector())


def expose() -> bytes:
    return generate_latest(registry)


async def refresh() -> Snapshot:
    """Recomputes the snapshot from the database. One pass: about ten bounded queries."""
    from .db.models import CostLedger, MemoryFact, Project, Release, Run, RunEvent, WorkItem
    from .db.session import session_scope
    from .services import effective_policy, first_pass_merge_rate

    now = utcnow()
    fresh = Snapshot()
    async with session_scope(orgs="*") as session:
        projects = {p.id: p for p in (await session.execute(select(Project))).scalars()}
        slug = {pid: p.slug for pid, p in projects.items()}

        active = await session.execute(
            select(func.count()).select_from(Run).where(Run.status.in_(["queued", "running"]))
        )
        fresh.runs_active = float(active.scalar_one())
        by_status = await session.execute(select(Run.status, func.count()).group_by(Run.status))
        fresh.runs_total = {str(st): float(n) for st, n in by_status.all()}

        since = now - timedelta(days=30)
        started = await session.execute(
            select(Run.created_at, Run.started_at).where(Run.started_at.is_not(None), Run.created_at >= since)
        )
        durations = [max(0.0, (_aware(s) - _aware(c)).total_seconds()) for c, s in started.all() if s and c]
        fresh.run_start = _buckets(durations, START_SECONDS)

        costs = await session.execute(
            select(
                CostLedger.project_id, CostLedger.stage_role, CostLedger.model, func.sum(CostLedger.cost_usd)
            )
            .where(CostLedger.project_id.is_not(None))  # an organisation-level line has no project label
            .group_by(CostLedger.project_id, CostLedger.stage_role, CostLedger.model)
        )
        fresh.cost = {
            (slug.get(str(pid), "?"), str(stage or ""), str(model or "")): float(total or 0.0)
            for pid, stage, model, total in costs.all()
        }
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today = await session.execute(
            select(CostLedger.project_id, func.sum(CostLedger.cost_usd))
            .where(CostLedger.ts >= midnight, CostLedger.project_id.is_not(None))
            .group_by(CostLedger.project_id)
        )
        fresh.daily_cost = {slug.get(str(pid), "?"): float(total or 0.0) for pid, total in today.all()}
        for pid, project in projects.items():
            policy = await effective_policy(session, pid)
            budget = policy.budgets.daily_project_usd
            if budget:
                fresh.daily_budget[project.slug] = float(budget)
            rate = await first_pass_merge_rate(session, pid)
            if rate is not None:
                fresh.first_pass[project.slug] = float(rate)

        releases = await session.execute(select(Release.status, func.count()).group_by(Release.status))
        fresh.releases = {str(st): float(n) for st, n in releases.all()}

        closed = await session.execute(
            select(WorkItem.created_at, WorkItem.closed_at).where(
                WorkItem.closed_at.is_not(None), WorkItem.closed_at >= now - timedelta(days=90)
            )
        )
        fresh.lead_time = _buckets(
            [max(0.0, (_aware(f) - _aware(c)).total_seconds()) for c, f in closed.all() if f and c],
            DELIVERY_SECONDS,
        )

        denials = await session.execute(
            select(Run.project_id, func.count())
            .select_from(RunEvent)
            .join(Run, Run.id == RunEvent.run_id)
            .where(
                RunEvent.type == "session/request_permission",
                RunEvent.payload["allowed"].as_boolean().is_(False),
            )
            .group_by(Run.project_id)
        )
        fresh.denials = {slug.get(str(pid), "?"): float(n) for pid, n in denials.all()}

        pending = await session.execute(
            select(func.count()).select_from(MemoryFact).where(MemoryFact.status == "pending")
        )
        fresh.memory_pending = float(pending.scalar_one())

        empty: Counter[str] = Counter()
        packs = await session.execute(select(Run.project_id, Run.context_pack).where(Run.created_at >= since))
        for pid, pack in packs.all():
            if pack is not None and not (pack.get("facts") or pack.get("items") or pack.get("snippets")):
                empty[slug.get(str(pid), "?")] += 1
        fresh.context_pack_empty = {k: float(v) for k, v in empty.items()}

    fresh.train_frozen, fresh.train_status = await _trains(list(slug.values()))
    fresh.refreshed_at = now.timestamp()
    _Current.snapshot = fresh
    return fresh


async def _trains(slugs: list[str]) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], float]]:
    """The state of the trains lives in Temporal: one `status_query` per (project, environment), bounded.

    S26-03: it asked for `status`, a query the train does not define, and read `state`, a field it
    does not return; the failure was swallowed below, so the train series were always empty.
    """
    from .temporal import get_temporal, train_id

    frozen: dict[tuple[str, str], float] = {}
    states: dict[tuple[str, str], float] = {}
    temporal = get_temporal()
    for project in slugs:
        for env in ENVIRONMENTS:
            try:
                live = await asyncio.wait_for(
                    temporal.query(train_id(project, env), TRAIN_STATUS_QUERY), timeout=2.0
                )
            except Exception as exc:  # no train, or Temporal is slow: the series is missing, the API lives
                logger.debug("train without state", project=project, env=env, error=str(exc)[:80])
                continue
            if not isinstance(live, dict):
                continue
            frozen[(project, env)] = 1.0 if live.get("frozen") else 0.0
            states[(project, env)] = float(TRAIN_STATES.get(str(live.get("status", "")), -1))
    return frozen, states


def _aware(moment: Any) -> Any:
    from datetime import UTC

    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def refresh_loop(interval_s: float) -> None:
    """Background task: a failed refresh does not kill the API, it shows in `refreshed_at`."""
    while True:
        try:
            await refresh()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("metrics refresh failed", error=str(exc)[:200])
        await asyncio.sleep(interval_s)


def _stub_for_type_checkers() -> None:  # pragma: no cover
    _ = defaultdict, contextlib
