"""The connector, operation and action lists page and filter in SQL (ADR 0206).

Every list is read page after page: each row is reached once, none is skipped or repeated across a
page break, rows sharing one timestamp (or one name prefix) stay in a total order, and the filters
the console used to apply in the browser run in the query.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from tranibor_core.domain import utcnow

ORG = "/api/v1/orgs/northwind"
PROJECT = "/api/v1/projects/billing-api"


async def _seed_connectors(count: int) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import Organization, OrgConnector
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "northwind"))
        ).scalar_one()
        for index in range(count):
            session.add(
                OrgConnector(
                    org_id=org_id,
                    name=f"conn-{index:03d}",
                    kind="mcp" if index % 3 == 0 else "memory",
                    type="mcp" if index % 3 == 0 else "okf",
                    config={},
                )
            )


async def _seed_operations(per_connector: int, connectors: int) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import ConnectorOperation, OrgConnector
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        for connector in (await session.execute(select(OrgConnector).order_by(OrgConnector.name))).scalars():
            if int(connector.name.split("-")[1]) >= connectors:
                continue
            for index in range(per_connector):
                session.add(
                    ConnectorOperation(
                        org_id=connector.org_id,
                        connector_id=connector.id,
                        name=f"op_{index:03d}",
                        access="read",
                        policy="allowed",
                        groups=["ops"] if index % 2 else [],
                    )
                )


async def _seed_actions(count: int, *, same_instant: bool = False) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import Action, Project
    from tranibor_api.db.session import session_scope

    start = utcnow()
    async with session_scope() as session:
        project = (await session.execute(select(Project).where(Project.slug == "billing-api"))).scalar_one()
        for index in range(count):
            session.add(
                Action(
                    org_id=project.org_id,
                    project_id=project.id,
                    origin="agent",
                    kind="demo.write",
                    title=f"action {index:03d}",
                    status="pending_approval" if index % 4 == 0 else "executed",
                    created_at=start if same_instant else start + timedelta(seconds=index),
                )
            )


async def _all(client: AsyncClient, url: str, key: str, **params: Any) -> list[Any]:
    seen: list[Any] = []
    cursor: str | None = None
    while True:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        response = await client.get(url, params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        seen += [key_of(item, key) for item in body["items"]]
        if not body["meta"]["has_more"]:
            assert body["meta"]["next_cursor"] is None
            return seen
        cursor = body["meta"]["next_cursor"]


def key_of(item: dict[str, Any], key: str) -> Any:
    return tuple(item[part] for part in key.split("+")) if "+" in key else item[key]


@pytest.mark.parametrize("url", [f"{ORG}/connectors", f"{PROJECT}/org-connectors"])
async def test_every_connector_is_reachable_once_by_name(
    client: AsyncClient, project: dict[str, Any], url: str
) -> None:
    await _seed_connectors(230)
    first = (await client.get(url, params={"limit": 50})).json()
    assert len(first["items"]) == 50 and first["meta"]["has_more"] is True
    seen = await _all(client, url, "name", limit=50)
    assert seen == [f"conn-{index:03d}" for index in range(230)], "by name, each once"


@pytest.mark.parametrize("url", [f"{ORG}/connectors", f"{PROJECT}/org-connectors"])
async def test_the_connector_filters_run_in_the_query(
    client: AsyncClient, project: dict[str, Any], url: str
) -> None:
    await _seed_connectors(30)
    assert await _all(client, url, "name", kind="mcp", limit=4) == [f"conn-{i:03d}" for i in range(0, 30, 3)]
    assert await _all(client, url, "name", q="CONN-02", limit=3) == [f"conn-{i:03d}" for i in range(20, 30)]
    assert await _all(client, url, "name", q="%") == [], "LIKE wildcards are escaped"
    assert await _all(client, url, "name", q="conn-00", kind="memory", limit=5) == [
        f"conn-{i:03d}" for i in range(30) if i % 3 and i < 10
    ]


async def test_the_organisation_list_filters_on_the_type(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _seed_connectors(12)
    assert await _all(client, f"{ORG}/connectors", "name", type="okf") == [
        f"conn-{i:03d}" for i in range(12) if i % 3
    ]


async def test_a_foreign_or_malformed_cursor_is_refused(client: AsyncClient, project: dict[str, Any]) -> None:
    for url in (f"{ORG}/connectors", f"{PROJECT}/operations", f"{PROJECT}/actions"):
        assert (await client.get(url, params={"cursor": "not-a-cursor"})).status_code == 422
        assert (await client.get(url, params={"limit": 0})).status_code == 422


async def test_every_operation_is_reachable_once_and_only_those_the_project_sees(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    # 5 connectors x 60 operations, half of them limited to a group the project is not in.
    await _seed_connectors(5)
    await _seed_operations(60, 5)
    seen = await _all(client, f"{PROJECT}/operations", "connector+operation", limit=25)
    expected = [(f"conn-{c:03d}", f"op_{o:03d}") for c in range(5) for o in range(60) if not o % 2]
    assert seen == expected and len(set(seen)) == 150
    assert await _all(client, f"{PROJECT}/operations", "operation", connector="conn-002", limit=7) == [
        f"op_{o:03d}" for o in range(0, 60, 2)
    ]
    assert await _all(client, f"{PROJECT}/operations", "connector+operation", q="op_01", limit=3) == [
        (f"conn-{c:03d}", f"op_{o:03d}") for c in range(5) for o in range(10, 20) if not o % 2
    ]


@pytest.mark.parametrize("same_instant", [False, True])
async def test_every_action_is_reachable_once_newest_first(
    client: AsyncClient, project: dict[str, Any], same_instant: bool
) -> None:
    await _seed_actions(130, same_instant=same_instant)
    for url in (f"{PROJECT}/actions", f"{ORG}/actions"):
        first = (await client.get(url, params={"limit": 50})).json()
        assert len(first["items"]) == 50 and first["meta"]["has_more"] is True
        seen = await _all(client, url, "id", limit=50)
        assert len(seen) == 130 and len(set(seen)) == 130
        if not same_instant:
            titles = await _all(client, url, "title", limit=50)
            assert titles[0] == "action 129" and titles[-1] == "action 000"


async def test_the_action_filters_run_in_the_query(client: AsyncClient, project: dict[str, Any]) -> None:
    await _seed_actions(20)
    for url in (f"{PROJECT}/actions", f"{ORG}/actions"):
        waiting = await _all(client, url, "title", status="pending_approval", limit=2)
        assert waiting == [f"action {i:03d}" for i in range(16, -1, -4)]
    assert await _all(
        client, f"{ORG}/actions", "title", project="billing-api", status="pending_approval"
    ) == [f"action {i:03d}" for i in range(16, -1, -4)]
    assert await _all(client, f"{ORG}/actions", "title", project="nowhere") == []
