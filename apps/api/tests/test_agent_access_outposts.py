# SPDX-License-Identifier: Apache-2.0
"""An agent's access says where a tool lives (S65-17b, ADR 0149 §10, amending ADR 0127 §6).

For a connector tool reached through an outpost, `GET /orgs/{org}/agents/{slug}/access` names the
outpost and the alias, the outpost's status, and whether an instance that serves the alias polled
within the poll window: the presence the call's own route reads. A person reads, before a run, that a
tool its agent names will not answer. The field is additive: the entry of a direct connector's tool
carries no `outpost` key. The presence is read once per outpost, however many tools live there, and an
outpost the caller's organisation does not have is never named, whatever a connector's row says.

They run on SQLite and, with `CHOREGOS_TEST_SUITE_ON_POSTGRES`, on PostgreSQL under row-level
security, where the outposts' registry and an outpost's instances are read under their own scopes.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest
from choregos_contracts import load_openapi
from httpx import AsyncClient
from jsonschema import Draft202012Validator

from .kits import outpost_daemon as daemon_kit
from .kits import outpost_route as route
from .kits import outposts as kit
from .kits.outpost_route import ALIAS, CONNECTOR, ORG, SLUG

AGENT = "coordinator"
ACCESS = f"{ORG}/agents/{AGENT}/access"
#: An outpost of the organisation that no instance ever polled, and the connector at it.
LAB, FORGE, FORGE_ALIAS = "lab", "forge", "forge"
#: A connector the platform reaches directly: its tool's entry is the one ADR 0127 described.
DIRECT = "supplier"
#: A second connector at `factory-floor`, on the same alias.
ARCHIVE = "tracker-archive"


@pytest.fixture(autouse=True)
def door(monkeypatch: pytest.MonkeyPatch) -> kit.Door:
    return route.switch_on(monkeypatch)


async def _operations(connector: str, *names: str) -> None:
    """Reads of a connector, opened: what discovery and an administrator would have left."""
    from choregos_api.db.models import ConnectorOperation, OrgConnector
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        row = (await session.execute(select(OrgConnector).where(OrgConnector.name == connector))).scalar_one()
        for name in names:
            session.add(
                ConnectorOperation(
                    org_id=row.org_id,
                    connector_id=row.id,
                    name=name,
                    access="read",
                    policy="allowed",
                    groups=[],
                    input_schema={"type": "object"},
                )
            )


async def _connector(name: str, type_: str, config: dict[str, Any], outpost_id: str | None = None) -> None:
    """A connector row written as an administrator, or a row edited behind the API, would leave it."""
    from choregos_api.db.models import Organization, OrgConnector
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "varga"))
        ).scalar_one()
        connector = OrgConnector(org_id=org_id, name=name, kind="mcp", type=type_, config=config)
        connector.outpost_id = outpost_id
        session.add(connector)


async def _scene(client: AsyncClient, connectors: list[str]) -> None:
    """`factory-floor`, whose approved instance polled for `tracker`, with two connectors at it of two
    reads each; `lab`, which no instance ever polled, with two reads of a connector at it; a direct
    connector; and an agent whose version names the connectors given."""
    daemon = await daemon_kit.approved(client)
    await route.seen(client, daemon, route.outpost())
    await route.declare(client)
    await _operations(CONNECTOR, "search_issues", "get_issue")
    await route.declare(client, ARCHIVE)
    await _operations(ARCHIVE, "search_archive", "get_archived")
    await kit.create(client, LAB)
    await route.declare(client, FORGE, outpost=LAB, endpoint=FORGE_ALIAS)
    await _operations(FORGE, "list_repos", "get_repo")
    await _connector(DIRECT, "mcp", {"url": "https://tools.example.com/mcp"})
    await _operations(DIRECT, "track_order")
    await _agent(client, connectors)


async def _agent(client: AsyncClient, connectors: list[str]) -> None:
    spec = {"mcp_servers": [{"connector": name, "tools": ["*"]} for name in connectors]}
    body = {"slug": AGENT, "display_name": "Coordinator", "spec": spec}
    created = await client.post(f"{ORG}/agents", json=body)
    assert created.status_code == 201, created.text


async def _access(client: AsyncClient) -> dict[str, Any]:
    response = await client.get(ACCESS, params={"project": "billing-api"})
    assert response.status_code == 200, response.text
    return dict(response.json())


def _connector_tools(access: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t["name"]: t for t in access["tools"] if t["source"] == "connector"}


def _counted(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    """How many times the route's presence is read, by outpost."""
    from choregos_api.services import outposts

    reads: Counter[str] = Counter()
    real = outposts.presence

    async def counted(session: Any, outpost: Any, **kwargs: Any) -> Any:
        reads[outpost.slug] += 1
        return await real(session, outpost, **kwargs)

    monkeypatch.setattr(outposts, "presence", counted)
    return reads


def _is_the_contracts(body: dict[str, Any]) -> None:
    components = load_openapi()["components"]
    schema = {**components["schemas"]["AgentAccess"], "components": components}
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(body)]
    assert not errors, f"not the contract's `AgentAccess`: {errors}"


async def test_a_tool_at_an_outpost_names_it_its_alias_and_whether_it_is_online(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _scene(client, [CONNECTOR, FORGE, DIRECT])
    access = await _access(client)
    _is_the_contracts(access)
    tools = _connector_tools(access)
    online = {"name": SLUG, "endpoint": ALIAS, "status": "active", "online": True}
    offline = {"name": LAB, "endpoint": FORGE_ALIAS, "status": "active", "online": False}
    assert {name: tool.get("outpost") for name, tool in tools.items()} == {
        f"{CONNECTOR}__search_issues": online,
        f"{CONNECTOR}__get_issue": online,
        f"{FORGE}__list_repos": offline,
        f"{FORGE}__get_repo": offline,
        f"{DIRECT}__track_order": None,
    }
    # Additive: a direct connector's entry is the one the access answered before ADR 0149.
    assert tools[f"{DIRECT}__track_order"] == {
        "name": f"{DIRECT}__track_order",
        "source": "connector",
        "origin": "agent",
        "access": "read",
        "reason": None,
    }
    assert all("outpost" not in t for t in access["tools"] if t["source"] != "connector")


async def test_the_presence_is_read_once_per_outpost_not_once_per_tool(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Four tools live at `factory-floor`, under two connectors; two at `lab`.
    await _scene(client, [CONNECTOR, ARCHIVE, FORGE, DIRECT])
    reads = _counted(monkeypatch)
    access = await _access(client)
    assert reads == Counter({SLUG: 1, LAB: 1}), "one read per outpost, whatever lives there"
    where = Counter((t.get("outpost") or {}).get("name") for t in _connector_tools(access).values())
    assert where == Counter({SLUG: 4, LAB: 2, None: 1})


async def test_an_outpost_the_organisation_does_not_have_is_never_named(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import Organization, Outpost
    from choregos_api.db.session import session_scope

    theirs = "their-floor"
    async with session_scope(orgs="*") as session:
        other = Organization(slug="acme", name="Acme")
        session.add(other)
        await session.flush()
        outpost = Outpost(
            org_id=other.id, slug=theirs, display_name="Their floor", status="active", capabilities=["tools"]
        )
        session.add(outpost)
        await session.flush()
        their_id = outpost.id
    # A row edited behind the API: a connector of `varga` that names, and routes to, `acme`'s outpost.
    await _connector("borrowed", "mcp-outpost", {"outpost": theirs, "endpoint": ALIAS}, outpost_id=their_id)
    await _operations("borrowed", "search_issues")
    await _agent(client, ["borrowed"])
    reads = _counted(monkeypatch)
    response = await client.get(ACCESS, params={"project": "billing-api"})
    assert response.status_code == 200, response.text
    assert theirs not in response.text and their_id not in response.text
    (tool,) = _connector_tools(response.json()).values()
    assert tool["outpost"] == {"name": None, "endpoint": ALIAS, "status": None, "online": False}
    assert not reads, "the presence of another organisation's outpost is not read"


async def test_a_paused_outpost_says_so_and_none_is_online_while_outposts_are_off(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    await _scene(client, [CONNECTOR, FORGE])
    assert (await client.post(f"{ORG}/outposts/{LAB}/pause")).status_code == 200
    tools = _connector_tools(await _access(client))
    assert tools[f"{FORGE}__list_repos"]["outpost"]["status"] == "paused"
    assert tools[f"{CONNECTOR}__get_issue"]["outpost"]["online"] is True
    kit.configure(monkeypatch, outposts=None)
    reads = _counted(monkeypatch)
    tools = _connector_tools(await _access(client))
    assert {t["outpost"]["name"]: t["outpost"]["online"] for t in tools.values()} == {SLUG: False, LAB: False}
    assert not reads, "nothing is handed to an outpost: no presence is read"
