"""A tool under validation becomes an action; the decision inbox (ADR 0035, S20-02).

The agent calls `fournisseur__commander_poste`: nothing reaches the server, an action is proposed in
its name (`agent:<slug>`), and it waits in the organisation's inbox. The agent's owner does not
approve it; another person, authenticated again, does.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import login
from .test_broker import TOOLS, _prepare


@pytest.fixture
def supplier(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    server = FakeMcpServer(tools=[dict(o) for o in TOOLS], token="cle-fournisseur")
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)
    monkeypatch.setenv("CONNECTOR_TEST_CLE_FOURNISSEUR", "cle-fournisseur")
    yield server


async def test_a_tool_under_validation_proposes_an_action_and_nothing_reaches_the_server(
    client: AsyncClient, project: dict[str, Any], supplier: Any, app: Any
) -> None:
    from choregos_api.temporal import get_temporal

    base, headers = await _prepare(client, project, ["*"])
    call = await client.post(
        f"{base}/fournisseur__commander_poste", headers=headers, json={"model": "portable-14"}
    )
    assert call.status_code == 200 and call.json()["status_code"] == 202, call.text
    action_id = call.json()["result"]["action"]
    assert call.json()["result"]["decision_path"] == f"/p/billing-api/actions/{action_id}"
    assert supplier.calls == [], "nothing reaches the server before the decision"

    inbox = (await client.get("/api/v1/orgs/varga/actions", params={"status": "pending_approval"})).json()
    (waiting,) = [a for a in inbox if a["id"] == action_id]
    assert waiting["origin"] == "tool"
    assert waiting["proposed_by"]["id"] == "agent:coordinateur"
    assert waiting["effects"][0]["with"] == {
        "connector": "fournisseur",
        "operation": "commander_poste",
        "arguments": {"model": "portable-14"},
    }

    # The agent's owner (who created it) does not approve its agent's writes.
    decision_url = f"/api/v1/projects/{project['id']}/actions/{action_id}/decision"
    refused = await client.post(decision_url, json={"decision": "approve"})
    assert refused.status_code == 422 and "separation of duties" in refused.text

    member = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "hr@varga.dev", "role": "project_owner"}
    )
    assert member.status_code in {200, 201}
    hr = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(hr, "hr@varga.dev")
        approved = await hr.post(decision_url, json={"decision": "approve"})
    finally:
        await hr.aclose()
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert f"action-{action_id}" in get_temporal().started  # type: ignore[attr-defined]
    assert supplier.calls == [], "even approved: Temporal runs it, not the request"


async def test_a_proposal_counts_towards_the_runs_ceiling(
    client: AsyncClient, project: dict[str, Any], supplier: Any
) -> None:
    """An agent loop does not fill the inbox: the run's call ceiling holds too."""
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    base, headers = await _prepare(client, project, ["*"])
    async with session_scope() as session:
        policy = (
            (
                await session.execute(
                    select(PolicyDef)
                    .where(PolicyDef.project_id == project["id"])
                    .order_by(PolicyDef.version.desc())
                )
            )
            .scalars()
            .first()
        )
        assert policy is not None
        document = dict(policy.json_doc)
        document["budgets"] = {**(document.get("budgets") or {}), "tool_calls_per_run": 1}
        policy.json_doc = document
    assert (
        await client.post(f"{base}/fournisseur__commander_poste", headers=headers, json={})
    ).status_code == 200
    assert (
        await client.post(f"{base}/fournisseur__commander_poste", headers=headers, json={})
    ).status_code == 429
