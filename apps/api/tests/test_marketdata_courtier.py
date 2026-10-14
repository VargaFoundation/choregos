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
CLE = "cle-marketdata-du-coffre"


@pytest.fixture
def marche(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.marketdata import FakeMarketData

    faux = FakeMarketData()
    monkeypatch.setattr(choregos_adapters, "FAUX_MARKETDATA", faux)
    monkeypatch.setenv("CONNECTOR_TEST_MARKETDATA_KEY", CLE)
    yield faux


async def _preparer(client: AsyncClient, project: dict[str, Any]) -> tuple[str, dict[str, str]]:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    corps = {
        "name": "cours",
        "kind": "marketdata",
        "type": "stooq",
        "secret_refs": {"api_key": "env:CONNECTOR_TEST_MARKETDATA_KEY"},
    }
    cree = await client.post(f"{ORG}/connectors", json=corps)
    assert cree.status_code == 201, cree.text
    assert CLE not in cree.text
    assert {o["name"]: o["policy"] for o in cree.json()["operations"]} == {
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
    jeton = mint_run_token(
        "run-marketdata-1", project_slug="billing-api", work_item_key="varga/x#7", ttl_minutes=30
    )
    return "/api/v1/internal/runs/run-marketdata-1/tools", {"Authorization": f"Bearer {jeton}"}


async def test_a_run_reads_prices_through_the_platform_metered_with_a_digest(
    client: AsyncClient, project: dict[str, Any], marche: Any
) -> None:
    from choregos_api.db.models import CostLedger, Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    base, entetes = await _preparer(client, project)
    annonces = (await client.get(base, headers=entetes)).json()["tools"]
    assert {o["name"] for o in annonces} == {"cours__get_bars", "cours__get_instrument"}

    arguments = {"symbol": "AAPL", "start": "2024-01-02", "end": "2024-01-12"}
    reponse = await client.post(f"{base}/cours__get_bars", headers=entetes, json=arguments)
    assert reponse.status_code == 200 and reponse.json()["status_code"] == 200, reponse.text
    assert CLE not in reponse.text, "the key does not come back to the pod"
    resultat = reponse.json()["result"]["structuredContent"]
    assert resultat["symbol"] == "AAPL" and len(resultat["bars"]) == 9
    assert marche.calls == [("get_bars", arguments)]

    # a symbol that would change the URL is refused by the schema, before the provider, and costs nothing
    hostile = await client.post(
        f"{base}/cours__get_bars", headers=entetes, json={**arguments, "symbol": "AAPL/../x"}
    )
    assert hostile.status_code == 400 and len(marche.calls) == 1

    async with session_scope() as session:
        (ligne,) = (
            await session.execute(select(CostLedger).where(CostLedger.run_id == "run-marketdata-1"))
        ).scalars()
        evenements = (
            (await session.execute(select(Event).where(Event.type == "choregos.tool.called"))).scalars().all()
        )
    assert (ligne.kind, ligne.provider, ligne.model) == ("tool", "mcp:cours", "cours__get_bars")
    (evenement,) = [e for e in evenements if e.subject == "run-marketdata-1"]

    def empreinte(valeur: Any) -> str:
        brut = json.dumps(valeur, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(brut.encode()).hexdigest()

    assert evenement.payload["request_sha256"] == empreinte(arguments)
    assert evenement.payload["response_sha256"] == empreinte(reponse.json()["result"])


async def test_an_unknown_symbol_is_a_named_refusal(
    client: AsyncClient, project: dict[str, Any], marche: Any
) -> None:
    base, entetes = await _preparer(client, project)
    reponse = await client.post(f"{base}/cours__get_instrument", headers=entetes, json={"symbol": "NOPE1"})
    assert reponse.json()["status_code"] == 422 and "unknown symbol" in reponse.text
