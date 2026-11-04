"""The console reads each list of the agents' pages in one request (S30-04).

Before, the AI clients' page read the clients of every external agent one by one, the registry had
no metrics column, and a project's page read every workflow to find its implicit agents.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient


async def _agent(client: AsyncClient, slug: str, kind: str = "internal") -> None:
    created = await client.post(
        "/api/v1/orgs/varga/agents",
        json={"slug": slug, "kind": kind, "display_name": slug, "spec": {}},
    )
    assert created.status_code == 201, created.text


async def test_the_organisation_s_agent_clients_come_in_one_list_with_their_agent(
    client: AsyncClient, admin: str
) -> None:
    for slug in ("claude-of-lea", "cursor-of-marc"):
        await _agent(client, slug, "external")
        token = await client.post("/api/v1/me/tokens", json={"name": slug, "scopes": ["mcp:read"]})
        assert token.status_code == 201, token.text
        attached = await client.post(
            f"/api/v1/orgs/varga/agents/{slug}/credentials",
            json={"kind": "token", "token_id": token.json()["id"]},
        )
        assert attached.status_code == 201, attached.text
    listing = await client.get("/api/v1/orgs/varga/agent-credentials")
    assert listing.status_code == 200, listing.text
    assert [(c["agent"], c["token_name"]) for c in listing.json()] == [
        ("claude-of-lea", "claude-of-lea"),
        ("cursor-of-marc", "cursor-of-marc"),
    ]


async def test_the_registry_s_metrics_come_in_one_list(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import CostLedger, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    await _agent(client, "writer")
    await _agent(client, "reader")
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="BILLING-9", title="T", state="ready")
        session.add(item)
        await session.flush()
        for rank, status in enumerate(("succeeded", "failed", "running")):
            session.add(
                Run(
                    id=f"run-m{rank}",
                    work_item_id=item.id,
                    project_id=project["id"],
                    stage_role="implement",
                    status=status,
                    agent_slug="writer",
                    agent_version=1,
                )
            )
        await session.flush()
        session.add(
            CostLedger(project_id=project["id"], run_id="run-m0", kind="model", cost_usd=0.5, ts=utcnow())
        )
    rows = {m["agent"]: m for m in (await client.get("/api/v1/orgs/varga/agent-metrics")).json()}
    assert rows["writer"]["runs"] == 3
    assert rows["writer"]["success_rate"] == pytest.approx(0.5), "a running run is not finished"
    assert rows["writer"]["cost_usd"] == pytest.approx(0.5)
    assert (rows["reader"]["runs"], rows["reader"]["success_rate"]) == (0, None)


async def test_a_project_s_implicit_agents_are_computed_by_the_api(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import WorkflowDef
    from choregos_api.db.session import session_scope

    await _agent(client, "coordinator")
    actors = {
        "coordinator": {"type": "agent", "agent": "coordinator@1"},
        "checker": {"type": "agent", "role": "verify", "model": "profile:fast", "max_turns": 12},
        "owner": {"type": "human"},
    }
    async with session_scope() as session:
        session.add(
            WorkflowDef(
                project_id=project["id"],
                name="hr",
                version=2,
                yaml="",
                json_doc={"actors": actors},
                checksum="c2",
                is_active=True,
            )
        )
        # An older version is not what runs: its actors are not listed.
        session.add(
            WorkflowDef(
                project_id=project["id"],
                name="hr",
                version=1,
                yaml="",
                json_doc={"actors": {"former": {"type": "agent"}}},
                checksum="c1",
                is_active=False,
            )
        )
    response = await client.get(f"/api/v1/projects/{project['id']}/implicit-agents")
    assert response.status_code == 200, response.text
    hr_ones = [a for a in response.json() if a["workflow"] == "hr"]
    assert hr_ones == [
        {
            "workflow": "hr",
            "actor": "checker",
            "role": "verify",
            "model": "profile:fast",
            "backend": None,
            "max_turns": 12,
            "max_minutes": None,
        }
    ]
