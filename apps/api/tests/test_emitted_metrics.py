"""The metrics exist, and the dashboards only cite what exists.

Before 2026-09-24: a ServiceMonitor, four alerts and six dashboards read sixteen `choregos_*`
series that no process emitted. Six empty dashboards, delivered as if they worked. The first test
prevents that from coming back; the second checks that `/metrics` carries the series, computed
from the database.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

from httpx import AsyncClient

ROOT = pathlib.Path(__file__).resolve().parents[3]
DASHBOARDS = ROOT / "charts" / "choregos" / "dashboards"
ALERTS = ROOT / "charts" / "choregos" / "templates" / "monitoring.yaml"
CHOREGOS = re.compile(r"choregos_[a-z_]+")
SUFFIXES = ("_bucket", "_count", "_sum")


def _base(name: str) -> str:
    for suffix in SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def test_the_dashboards_and_the_alerts_only_cite_emitted_metrics() -> None:
    from choregos_api.metrics import NAMES

    cited: dict[str, set[str]] = {}
    for file in sorted(DASHBOARDS.glob("*.json")):
        text = json.dumps(json.loads(file.read_text()))
        cited[file.name] = {_base(m) for m in CHOREGOS.findall(text)}
    cited["monitoring.yaml"] = {_base(m) for m in CHOREGOS.findall(ALERTS.read_text())}
    absent = {f: sorted(names - NAMES) for f, names in cited.items() if names - NAMES}
    assert not absent, f"metrics cited but never emitted: {absent}"
    assert sum(len(n) for n in cited.values()) >= 12, "the dashboards must still read series"


async def test_metrics_exposes_the_series_from_the_database(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import CostLedger, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.metrics import NAMES, refresh
    from choregos_core import utcnow

    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="varga/billing-api#9", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="varga-billing-api-9-t-implement-1",
                work_item_id=item.id,
                project_id=project["id"],
                transition_id="t-implement",
                stage_role="implement",
                attempt=1,
                actor="agent",
                backend="claude-code",
                model="platform/standard",
                status="running",
                started_at=utcnow(),
                stage_input={},
            )
        )
        session.add(
            CostLedger(
                project_id=project["id"],
                work_item_id=item.id,
                run_id=None,
                provider="anthropic",
                model="platform/standard",
                backend="claude-code",
                stage_role="implement",
                size="M",
                tokens_in=10,
                tokens_out=5,
                tokens_cached=0,
                cost_usd=0.5,
                cost_eur=0.46,
                fx_rate=0.92,
                ts=utcnow(),
                kind="model",
            )
        )
    await refresh()
    response = await client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    for name in NAMES:
        assert name in body, f"series missing from /metrics: {name}"
    assert "choregos_runs_active 1.0" in body
    assert (
        'choregos_cost_usd_total{model="platform/standard",project="billing-api",stage="implement"} 0.5'
        in body
    )
    assert 'choregos_project_daily_cost_usd{project="billing-api"} 0.5' in body
    assert "http_requests_total{" in body


async def test_every_response_carries_a_request_identifier(client: AsyncClient) -> None:
    """The API's log and the front end's are linked by this identifier."""
    without = await client.get("/healthz")
    assert len(without.headers["x-request-id"]) >= 12
    with_one = await client.get("/healthz", headers={"X-Request-Id": "req-from-the-front-42"})
    assert with_one.headers["x-request-id"] == "req-from-the-front-42"
