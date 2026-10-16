"""Per-project enablement of organisation connectors (ADR 0074, S40-09a).

An organisation connector is `all_projects` (the default: what happened before this ADR) or
`selected_projects`: only the projects an owner or an administrator turned it on for reach it. The
guard lives where a project reaches a connector: the broker (a run's tools), the project policy (the
governed actions), and the project's list of operations.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from .agent_scopes_kit import add_member, as_user, make_project
from .catalogue_kit import project_only

ORG = "/api/v1/orgs/varga"
OUTILS: list[dict[str, Any]] = [
    {"name": "lookup", "description": "read", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object"}},
    {"name": "order", "description": "write", "inputSchema": {"type": "object"}},
]  # fmt: skip


@pytest.fixture
def vendor(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    server = FakeMcpServer(outils=[dict(o) for o in OUTILS], jeton="vendor-key")
    monkeypatch.setattr(choregos_adapters, "FAUX_MCP", server)
    monkeypatch.setenv("CONNECTOR_ENABLEMENT_TEST", "vendor-key")
    yield server


async def _prepare(
    client: AsyncClient, project: dict[str, Any], **declare: Any
) -> tuple[str, dict[str, str]]:
    """A discovered server with `lookup` open, an agent that selects it, a run of that agent."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    body = {"name": "vendor", "type": "mcp", "config": {"url": "https://vendor.test/mcp"},
            "secret_refs": {"token": "env:CONNECTOR_ENABLEMENT_TEST"}, **declare}  # fmt: skip
    assert (await client.post(f"{ORG}/connectors", json=body)).status_code == 201
    assert (await client.post(f"{ORG}/connectors/vendor/discover")).status_code == 200
    opened = await client.patch(f"{ORG}/connectors/vendor/operations/lookup", json={"policy": "allowed"})
    assert opened.status_code == 200, opened.text
    spec = {"mcp_servers": [{"connector": "vendor", "tools": ["*"]}]}
    agent = await client.post(
        f"{ORG}/agents", json={"slug": "caller", "display_name": "Caller", "spec": spec}
    )
    assert agent.status_code == 201, agent.text
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="varga/x#1", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="run-enablement-1",
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="plan",
                transition_id="t-plan",
                status="running",
                agent_slug="caller",
                agent_version=1,
            )
        )
    token = mint_run_token(
        "run-enablement-1", project_slug=project["slug"], work_item_key="varga/x#1", ttl_minutes=30
    )
    return "/api/v1/internal/runs/run-enablement-1/tools", {"Authorization": f"Bearer {token}"}


async def _tools(client: AsyncClient, base: str, headers: dict[str, str]) -> list[str]:
    listed = await client.get(base, headers=headers)
    assert listed.status_code == 200, listed.text
    # `web_fetch` is the platform's own built-in (S40-01f), not a connector's: not part of what is enabled.
    return [t["name"] for t in listed.json()["tools"] if t["name"] != "web_fetch"]


def _enablement(project: dict[str, Any]) -> str:
    return f"/api/v1/projects/{project['id']}/org-connectors/vendor/enablement"


async def test_a_connector_declared_without_availability_reaches_every_project(
    client: AsyncClient, project: dict[str, Any], vendor: Any
) -> None:
    """The behaviour before ADR 0074: nothing changes for what exists."""
    base, headers = await _prepare(client, project)
    declared = (await client.get(f"{ORG}/connectors/vendor")).json()
    assert declared["availability"] == "all_projects" and declared["enabled_projects"] == []
    assert await _tools(client, base, headers) == ["vendor__lookup"]


async def test_restricting_a_connector_closes_the_broker_until_the_project_is_enabled(
    client: AsyncClient, project: dict[str, Any], vendor: Any
) -> None:
    base, headers = await _prepare(client, project)
    restricted = await client.patch(f"{ORG}/connectors/vendor", json={"availability": "selected_projects"})
    assert restricted.status_code == 200 and restricted.json()["availability"] == "selected_projects"
    assert restricted.json()["enabled_projects"] == [], "restricting turns it on for no project"
    assert await _tools(client, base, headers) == [], "the run sees no tool"
    called = await client.post(f"{base}/vendor__lookup", headers=headers, json={})
    assert called.status_code == 404 and vendor.appels == []

    assert (await client.put(_enablement(project))).status_code == 200
    assert await _tools(client, base, headers) == ["vendor__lookup"]
    again = await client.post(f"{base}/vendor__lookup", headers=headers, json={})
    assert again.status_code == 200 and vendor.appels == [("lookup", {})]

    assert (await client.delete(_enablement(project))).status_code == 204
    assert await _tools(client, base, headers) == []


async def test_switching_back_to_all_projects_keeps_the_rows_for_a_later_switch(
    client: AsyncClient, project: dict[str, Any], vendor: Any
) -> None:
    await _prepare(client, project, availability="selected_projects")
    await client.put(_enablement(project))
    await client.patch(f"{ORG}/connectors/vendor", json={"availability": "all_projects"})
    again = await client.patch(f"{ORG}/connectors/vendor", json={"availability": "selected_projects"})
    assert again.json()["enabled_projects"] == ["billing-api"]


async def test_an_action_on_a_connector_not_enabled_for_the_project_is_refused(
    client: AsyncClient, project: dict[str, Any], vendor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The project policy behind governed actions agrees with the broker."""
    from choregos_api.db.session import session_scope
    from choregos_api.services.connecteurs import politique_pour_le_projet

    await _prepare(client, project, availability="selected_projects")
    await client.patch(f"{ORG}/connectors/vendor/operations/order", json={"policy": "approval"})
    from choregos_api.db.models import Project

    async def policy(operation: str) -> str:
        async with session_scope() as session:
            row = await session.get(Project, project["id"])
            assert row is not None
            return await politique_pour_le_projet(session, row, "vendor", operation)

    assert await policy("order") == "forbidden", "not enabled: not even an approved action reaches it"
    assert await policy("lookup") == "forbidden"
    await client.put(_enablement(project))
    assert await policy("order") == "approval"
    assert await policy("lookup") == "allowed"

    effect = {"effect": "connector.call", "with": {"connector": "vendor", "operation": "order"}}
    body = {"kind": "vendor.order", "title": "Order", "params": {}, "effects": [effect]}
    await client.delete(_enablement(project))
    refused = await client.post(f"/api/v1/projects/{project['id']}/actions", json=body)
    assert refused.status_code >= 400, "an action condemned in advance is not proposed"
    await client.put(_enablement(project))
    accepted = await client.post(f"/api/v1/projects/{project['id']}/actions", json=body)
    assert accepted.status_code == 201, accepted.text


async def test_the_projects_operations_leave_out_a_connector_not_enabled(
    client: AsyncClient, project: dict[str, Any], vendor: Any
) -> None:
    await _prepare(client, project, availability="selected_projects")
    operations = f"/api/v1/projects/{project['id']}/operations"
    assert (await client.get(operations)).json() == []
    listed = (await client.get(f"/api/v1/projects/{project['id']}/org-connectors")).json()
    assert [(c["name"], c["enabled"], c["can_toggle"]) for c in listed] == [("vendor", False, True)]
    await client.put(_enablement(project))
    assert {o["operation"] for o in (await client.get(operations)).json()} == {"lookup", "order"}
    listed = (await client.get(f"/api/v1/projects/{project['id']}/org-connectors")).json()
    assert [(c["name"], c["enabled"]) for c in listed] == [("vendor", True)]


async def test_who_may_turn_a_connector_on(client: AsyncClient, project: dict[str, Any], vendor: Any) -> None:
    other = await make_project(client, "checkout")
    await _prepare(client, project, availability="selected_projects")
    await add_member(client, "owner@varga.dev", "project_owner", "billing-api")
    await add_member(client, "dev@varga.dev", "developer", "billing-api")

    await project_only(client, "dev@varga.dev")
    assert (await client.put(_enablement(project))).status_code == 403
    assert (await client.delete(_enablement(project))).status_code == 403
    listed = (await client.get(f"/api/v1/projects/{project['id']}/org-connectors")).json()
    assert listed[0]["can_toggle"] is False and "owner" in listed[0]["cannot_reason"]

    await project_only(client, "owner@varga.dev")
    foreign = f"/api/v1/projects/{other['id']}/org-connectors/vendor/enablement"
    assert (await client.put(foreign)).status_code in {403, 404}, "an owner of another project"
    assert (await client.put(_enablement(project))).status_code == 200
    assert (await client.put(_enablement(project))).status_code == 200, "idempotent"
    assert (await client.delete(foreign)).status_code in {403, 404}

    await as_user(client, "admin@varga.dev")
    assert (await client.put(foreign)).status_code == 200, "an administrator, any project"
    assert (await client.get(f"{ORG}/connectors/vendor")).json()["enabled_projects"] == [
        "billing-api",
        "checkout",
    ]
    unknown = await client.put(f"/api/v1/projects/{project['id']}/org-connectors/nope/enablement")
    assert unknown.status_code == 404


async def test_enabling_a_connector_available_to_all_projects_is_a_conflict(
    client: AsyncClient, project: dict[str, Any], vendor: Any
) -> None:
    await _prepare(client, project)
    refused = await client.put(_enablement(project))
    assert refused.status_code == 409 and "restricts it first" in refused.text
    assert (await client.delete(_enablement(project))).status_code == 409


async def test_disabling_a_connector_that_was_not_on_is_a_no_op(
    client: AsyncClient, project: dict[str, Any], vendor: Any
) -> None:
    await _prepare(client, project, availability="selected_projects")
    assert (await client.delete(_enablement(project))).status_code == 204


async def test_only_the_administrator_changes_availability(
    client: AsyncClient, project: dict[str, Any], vendor: Any
) -> None:
    await _prepare(client, project)
    await add_member(client, "owner@varga.dev", "project_owner", "billing-api")
    await project_only(client, "owner@varga.dev")
    refused = await client.patch(f"{ORG}/connectors/vendor", json={"availability": "selected_projects"})
    assert refused.status_code == 403
    await as_user(client, "admin@varga.dev")
    assert (await client.get(f"{ORG}/connectors/vendor")).json()["availability"] == "all_projects"


async def test_the_gestures_leave_audit_rows(
    client: AsyncClient, project: dict[str, Any], vendor: Any
) -> None:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await _prepare(client, project)
    await client.patch(f"{ORG}/connectors/vendor", json={"availability": "selected_projects"})
    await client.put(_enablement(project))
    await client.delete(_enablement(project))
    async with session_scope() as session:
        rows = (await session.execute(select(AuditLog))).scalars().all()
    by_action = {r.action: r for r in rows}
    assert {"org.connector.availability", "org.connector.enable", "org.connector.disable"} <= set(by_action)
    assert by_action["org.connector.enable"].payload["project"] == "billing-api"
    assert by_action["org.connector.availability"].payload["availability"] == "selected_projects"


async def test_installing_from_the_catalogue_stores_the_availability_and_hides_foreign_projects(
    client: AsyncClient, org: str, vendor: Any
) -> None:
    await make_project(client, "billing")
    await make_project(client, "checkout")
    await add_member(client, "owner@varga.dev", "project_owner", "billing")
    installed = await client.post(
        f"{ORG}/mcp-catalogue/github/install",
        json={"secret_refs": {"token": "env:CONNECTOR_ENABLEMENT_TEST"}, "availability": "selected_projects"},
    )
    assert installed.status_code == 201, installed.text
    assert installed.json()["availability"] == "selected_projects"
    for slug in ("billing", "checkout"):
        enabled = await client.put(f"/api/v1/projects/{slug}/org-connectors/github/enablement")
        assert enabled.status_code == 200, enabled.text

    def enablement(body: dict[str, Any]) -> dict[str, Any]:
        entry = next(e for e in body["entries"] if e["id"] == "github")
        return dict(entry["enablement"][0])

    admin_view = enablement((await client.get(f"{ORG}/mcp-catalogue")).json())
    assert admin_view == {
        "connector": "github",
        "availability": "selected_projects",
        "enabled_projects": ["billing", "checkout"],
    }
    await project_only(client, "owner@varga.dev")
    owner_view = enablement((await client.get(f"{ORG}/mcp-catalogue")).json())
    assert owner_view["enabled_projects"] == ["billing"], "a project the caller does not see is not named"
