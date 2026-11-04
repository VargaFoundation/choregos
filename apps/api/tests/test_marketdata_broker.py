"""A run reads market data through the broker (S38-03): the connector is declared with its key by
reference, the run sees `<connector>__get_bars`, the call is metered, the key never comes back, and
the event keeps the digest of what was served. Under the test fakes the provider is the scriptable
`FakeMarketData`; no network."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"
KEY = "cle-marketdata-du-coffre"


@pytest.fixture
def market(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.marketdata import FakeMarketData

    fake = FakeMarketData()
    monkeypatch.setattr(choregos_adapters, "FAKE_MARKETDATA", fake)
    monkeypatch.setenv("CONNECTOR_TEST_MARKETDATA_KEY", KEY)
    yield fake


async def _prepare(client: AsyncClient, project: dict[str, Any]) -> tuple[str, dict[str, str]]:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    body = {
        "name": "cours",
        "kind": "marketdata",
        "type": "stooq",
        "secret_refs": {"api_key": "env:CONNECTOR_TEST_MARKETDATA_KEY"},
    }
    created = await client.post(f"{ORG}/connectors", json=body)
    assert created.status_code == 201, created.text
    assert KEY not in created.text
    assert {o["name"]: o["policy"] for o in created.json()["operations"]} == {
        "get_bars": "allowed",
        "get_instrument": "allowed",
    }
    spec = {"mcp_servers": [{"connector": "cours", "tools": ["get_*"]}]}
    agent = await client.post(
        f"{ORG}/agents", json={"slug": "analyste", "display_name": "Analyste", "spec": spec}
    )
    assert agent.status_code == 201, agent.text
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="varga/x#7", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="run-marketdata-1",
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="plan",
                transition_id="t-plan",
                status="running",
                agent_slug="analyste",
                agent_version=1,
            )
        )
    token = mint_run_token(
        "run-marketdata-1", project_slug="billing-api", work_item_key="varga/x#7", ttl_minutes=30
    )
    return "/api/v1/internal/runs/run-marketdata-1/tools", {"Authorization": f"Bearer {token}"}


async def test_a_run_reads_prices_through_the_platform_metered_with_a_digest(
    client: AsyncClient, project: dict[str, Any], market: Any
) -> None:
    from choregos_api.db.models import CostLedger, Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    base, headers = await _prepare(client, project)
    announcements = (await client.get(base, headers=headers)).json()["tools"]
    assert {o["name"] for o in announcements if o["name"] != "web_fetch"} == {
        "cours__get_bars",
        "cours__get_instrument",
    }

    arguments = {"symbol": "AAPL", "start": "2024-01-02", "end": "2024-01-12"}
    response = await client.post(f"{base}/cours__get_bars", headers=headers, json=arguments)
    assert response.status_code == 200 and response.json()["status_code"] == 200, response.text
    assert KEY not in response.text, "the key does not come back to the pod"
    result = response.json()["result"]["structuredContent"]
    assert result["symbol"] == "AAPL" and len(result["bars"]) == 9
    assert market.calls == [("get_bars", arguments)]

    # a symbol that would change the URL is refused by the schema, before the provider, and costs nothing
    hostile = await client.post(
        f"{base}/cours__get_bars", headers=headers, json={**arguments, "symbol": "AAPL/../x"}
    )
    assert hostile.status_code == 400 and len(market.calls) == 1

    async with session_scope() as session:
        (row,) = (
            await session.execute(select(CostLedger).where(CostLedger.run_id == "run-marketdata-1"))
        ).scalars()
        events = (
            (await session.execute(select(Event).where(Event.type == "choregos.tool.called"))).scalars().all()
        )
    assert (row.kind, row.provider, row.model) == ("tool", "mcp:cours", "cours__get_bars")
    (event,) = [e for e in events if e.subject == "run-marketdata-1"]

    def digest(value: Any) -> str:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    assert event.payload["request_sha256"] == digest(arguments)
    assert event.payload["response_sha256"] == digest(response.json()["result"])


async def test_an_unknown_symbol_is_a_named_refusal(
    client: AsyncClient, project: dict[str, Any], market: Any
) -> None:
    base, headers = await _prepare(client, project)
    response = await client.post(f"{base}/cours__get_instrument", headers=headers, json={"symbol": "NOPE1"})
    assert response.json()["status_code"] == 422 and "unknown symbol" in response.text
