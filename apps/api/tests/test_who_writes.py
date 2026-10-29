"""Who writes, and with which account (S42-06g, ADR 0107 story 1).

The owner's question of 2026-10-28: a webhook starts a workflow on a Jira ticket, a triage agent runs;
is the ticket moved through the API or an MCP server, with my account or a service account? The API
now says it:
- a work item names who started it (`initiated_by`);
- a run names its agent version, who started it, and the organisation connectors its agent may call
  through the broker, each with the account it calls as — never a secret;
- a timeline entry the bookkeeping wrote to the tracker names the connector type and account (`via`).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from .egress_support import as_person
from .test_courtier import OUTILS

ORG = "/api/v1/orgs/varga"
RUN = "run-who-writes-1"


@pytest.fixture
def supplier(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    server = FakeMcpServer(outils=[dict(o) for o in OUTILS], jeton="supplier-key")
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)
    monkeypatch.setenv("CONNECTOR_TEST_SUPPLIER_KEY", "supplier-key")
    yield server


async def _connector(client: AsyncClient, name: str, *, basic_as: str | None = None) -> None:
    """An MCP connector, discovered, `suivi_commande` open; `basic_as` then switches it to Basic."""
    from choregos_api.db.models import OrgConnector
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    body = {
        "name": name,
        "type": "mcp",
        "config": {"url": f"https://{name}.test/mcp"},
        "secret_refs": {"token": "env:CONNECTOR_TEST_SUPPLIER_KEY"},
    }
    assert (await client.post(f"{ORG}/connectors", json=body)).status_code == 201
    assert (await client.post(f"{ORG}/connectors/{name}/discover")).status_code == 200
    opened = await client.patch(
        f"{ORG}/connectors/{name}/operations/suivi_commande", json={"policy": "allowed"}
    )
    assert opened.status_code == 200, opened.text
    if basic_as:
        async with session_scope(orgs="*") as session:
            row = (await session.execute(select(OrgConnector).where(OrgConnector.name == name))).scalar_one()
            row.config = {**row.config, "auth_scheme": "basic", "username": basic_as}


async def _run(client: AsyncClient, project: dict[str, Any], servers: list[dict[str, Any]]) -> str:
    """An agent whose version names `servers`, and a run of it on a new work item."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope

    agent = await client.post(
        f"{ORG}/agents", json={"slug": "triage", "display_name": "Triage", "spec": {"mcp_servers": servers}}
    )
    assert agent.status_code == 201, agent.text
    async with session_scope() as session:
        item = WorkItem(
            project_id=project["id"],
            tracker_key="XXX-12",
            title="Triage me",
            state="triage",
            initiated_by="tracker:jira",
        )
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id=RUN,
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="triage",
                status="running",
                agent_slug="triage",
                agent_version=1,
                initiated_by="tracker:jira",
            )
        )
        return item.id


# ───────────────────────────── a run ─────────────────────────────


async def test_a_run_names_its_agent_its_initiator_and_the_connectors_it_may_call(
    client: AsyncClient, project: dict[str, Any], supplier: Any
) -> None:
    await _connector(client, "supplier")
    await _connector(client, "wiki", basic_as="svc-choregos@acme.example")
    await _connector(client, "unused")
    await _run(
        client,
        project,
        [{"connector": "wiki", "tools": ["suivi_*"]}, {"connector": "supplier", "tools": ["suivi_commande"]}],
    )

    response = await client.get(f"/api/v1/runs/{RUN}")
    assert response.status_code == 200, response.text
    run = response.json()
    assert (run["agent_slug"], run["agent_version"], run["initiated_by"]) == ("triage", 1, "tracker:jira")
    assert run["tool_connectors"] == [
        {"name": "supplier", "type": "mcp", "auth_scheme": "bearer", "account": None},
        {"name": "wiki", "type": "mcp", "auth_scheme": "basic", "account": "svc-choregos@acme.example"},
    ], "the agent's selection, each connector once, `unused` absent"
    assert "supplier-key" not in response.text, "never the key"


async def test_a_project_reader_sees_the_connectors_without_reading_the_organisations(
    client: AsyncClient, project: dict[str, Any], supplier: Any
) -> None:
    await _connector(client, "supplier")
    await _run(client, project, [{"connector": "supplier", "tools": ["*"]}])
    async with as_person(client, "viewer@varga.dev", "viewer") as viewer:
        run = (await viewer.get(f"/api/v1/runs/{RUN}")).json()
    assert [c["name"] for c in run["tool_connectors"]] == ["supplier"]


async def test_a_tool_the_policy_closes_does_not_count(
    client: AsyncClient, project: dict[str, Any], supplier: Any
) -> None:
    """The connectors the broker would serve: one whose selected tools are all closed is not listed."""
    await _connector(client, "supplier")
    await _run(client, project, [{"connector": "supplier", "tools": ["stock"]}])
    run = (await client.get(f"/api/v1/runs/{RUN}")).json()
    assert run["tool_connectors"] == []


async def test_a_list_of_runs_does_not_resolve_connectors_for_each_run(
    client: AsyncClient, project: dict[str, Any], supplier: Any
) -> None:
    await _connector(client, "supplier")
    item_id = await _run(client, project, [{"connector": "supplier", "tools": ["*"]}])
    (run,) = (await client.get(f"/api/v1/work-items/{item_id}/runs")).json()
    assert run["agent_slug"] == "triage" and run["tool_connectors"] == []


# ───────────────────────────── a work item ─────────────────────────────


async def test_a_work_item_names_who_started_it(client: AsyncClient, project: dict[str, Any]) -> None:
    created = await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Quarterly review", "start": False}
    )
    assert created.status_code == 201, created.text
    item = (await client.get(f"/api/v1/work-items/{created.json()['id']}")).json()
    assert str(item["initiated_by"]).startswith("person:"), item["initiated_by"]


async def test_the_timeline_says_which_tracker_write_and_under_which_account(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import Event, HumanRequest, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_contracts import EventType
    from choregos_core import utcnow

    via = {"type": "jira", "account": "bot@acme.example"}
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="XXX-7", title="T", state="triage")
        session.add(item)
        await session.flush()
        request = HumanRequest(
            work_item_id=item.id,
            project_id=project["id"],
            kind="question",
            payload={"question": "Which currency?"},
            requested_at=utcnow(),
        )
        session.add(request)
        await session.flush()
        for type_, payload in (
            (EventType.WORKITEM_STATE_CHANGED, {"from": "inbox", "to": "triage", "via": via}),
            (EventType.WORKITEM_HUMAN_REQUESTED, {"kind": "question", "request_id": request.id, "via": via}),
            # An event recorded before S42-06g names no tracker write.
            (EventType.WORKITEM_STATE_CHANGED, {"from": "triage", "to": "ready"}),
        ):
            session.add(
                Event(
                    project_id=project["id"],
                    work_item_id=item.id,
                    type=str(type_),
                    payload=payload,
                    ts=utcnow(),
                )
            )
        item_id = item.id

    entries = (await client.get(f"/api/v1/work-items/{item_id}/timeline")).json()
    by_title = {e["title"]: e["via"] for e in entries}
    assert by_title["inbox → triage"] == via
    assert by_title["human request: question"] == via
    assert by_title["triage → ready"] is None
