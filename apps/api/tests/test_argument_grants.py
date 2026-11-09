"""A tool grant may bind its arguments (S60-06, ADR 0128): allowing `sync` on ONE application of an
Argo CD is not allowing it on every one. The constraint is a JSON Schema the call's arguments must
satisfy on top of the operation's own, written by the agent version, the organisation's rule and the
project's policy; the call must pass all of them, before anything is proposed or reaches the server.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"
ARGO_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "project": {"type": "string"},
        "revision": {"type": "string"},
    },
    "required": ["name"],
}
TOOLS: list[dict[str, Any]] = [
    {"name": "sync_application", "description": "syncs an application", "inputSchema": ARGO_SCHEMA,
     "annotations": {"readOnlyHint": True}},
    {"name": "delete_application", "description": "deletes an application", "inputSchema": ARGO_SCHEMA},
]  # fmt: skip
ONLY_SHOP = {"properties": {"name": {"const": "shop"}}, "required": ["name"]}
SYNC = "argo__sync_application"


@pytest.fixture
def argo(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    server = FakeMcpServer(tools=[dict(tool) for tool in TOOLS], token="argo-key")
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)
    monkeypatch.setenv("CONNECTOR_TEST_ARGO_KEY", "argo-key")
    yield server


async def _connector(client: AsyncClient) -> None:
    body = {"name": "argo", "type": "mcp", "config": {"url": "https://argo.test/mcp"},
            "secret_refs": {"token": "env:CONNECTOR_TEST_ARGO_KEY"}}  # fmt: skip
    assert (await client.post(f"{ORG}/connectors", json=body)).status_code == 201
    assert (await client.post(f"{ORG}/connectors/argo/discover")).status_code == 200
    for operation, policy in (("sync_application", "allowed"), ("delete_application", "approval")):
        opened = await client.patch(f"{ORG}/connectors/argo/operations/{operation}", json={"policy": policy})
        assert opened.status_code == 200, opened.text


async def _agent(
    client: AsyncClient, project: dict[str, Any], tools: list[Any]
) -> tuple[str, dict[str, str]]:
    """An agent whose version grants `tools` on argo, and a run of it: the run's tools route and headers."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    spec = {"mcp_servers": [{"connector": "argo", "tools": tools}]}
    agent = await client.post(
        f"{ORG}/agents", json={"slug": "deployer", "display_name": "Deployer", "spec": spec}
    )
    assert agent.status_code == 201, agent.text
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="varga/x#1", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="run-grant-1",
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="plan",
                transition_id="t-plan",
                status="running",
                agent_slug="deployer",
                agent_version=1,
            )
        )
    token = mint_run_token(
        "run-grant-1", project_slug="billing-api", work_item_key="varga/x#1", ttl_minutes=30
    )
    return "/api/v1/internal/runs/run-grant-1/tools", {"Authorization": f"Bearer {token}"}


async def _call(
    client: AsyncClient, base: str, headers: dict[str, str], tool: str, arguments: dict[str, Any]
) -> Any:
    return await client.post(f"{base}/{tool}", headers=headers, json=arguments)


def _refused_outside(response: Any, level: str, path: str) -> None:
    assert response.status_code == 400, response.text
    problem = response.json()
    assert problem["type"] == "arguments_outside_grant", problem
    assert (problem["code"], problem["level"], problem["path"]) == ("arguments_outside_grant", level, path)


async def test_a_grant_on_one_application_refuses_the_others_before_the_server(
    client: AsyncClient, project: dict[str, Any], argo: Any
) -> None:
    await _connector(client)
    base, headers = await _agent(client, project, [{"name": "sync_application", "arguments": ONLY_SHOP}])

    shop = await _call(client, base, headers, SYNC, {"name": "shop"})
    assert shop.status_code == 200 and shop.json()["status_code"] == 200, shop.text
    assert argo.calls == [("sync_application", {"name": "shop"})]

    _refused_outside(await _call(client, base, headers, SYNC, {"name": "billing"}), "agent", "/name")
    # An argument the grant is silent about stays free.
    assert (await _call(client, base, headers, SYNC, {"name": "shop", "revision": "v2"})).status_code == 200
    assert [name for name, _ in argo.calls] == ["sync_application"] * 2, (
        "a refused call never reached the server"
    )


async def test_the_project_placeholder_is_the_runs_project(
    client: AsyncClient, project: dict[str, Any], argo: Any
) -> None:
    await _connector(client)
    bound = {"properties": {"project": {"const": "{project}"}}, "required": ["project"]}
    base, headers = await _agent(client, project, [{"name": "sync_application", "arguments": bound}])

    ours = await _call(client, base, headers, SYNC, {"name": "x", "project": "billing-api"})
    assert ours.status_code == 200, ours.text
    _refused_outside(
        await _call(client, base, headers, SYNC, {"name": "x", "project": "other"}), "agent", "/project"
    )


async def test_the_project_narrows_on_top_of_the_agent_grant(
    client: AsyncClient, project: dict[str, Any], argo: Any
) -> None:
    await _connector(client)
    two = {"properties": {"name": {"enum": ["shop", "billing"]}}, "required": ["name"]}
    base, headers = await _agent(client, project, [{"name": "sync_application", "arguments": two}])
    assert (await _call(client, base, headers, SYNC, {"name": "billing"})).json()["status_code"] == 200

    narrowed = await client.put(
        f"/api/v1/projects/{project['id']}/operations/argo/sync_application",
        json={"policy": "allowed", "arguments": ONLY_SHOP},
    )
    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.json()["project_arguments"] == ONLY_SHOP

    _refused_outside(await _call(client, base, headers, SYNC, {"name": "billing"}), "project", "/name")
    assert (await _call(client, base, headers, SYNC, {"name": "shop"})).json()["status_code"] == 200
    # The project never widens: what the agent's grant refuses stays refused.
    _refused_outside(await _call(client, base, headers, SYNC, {"name": "other"}), "agent", "/name")


async def test_the_organisation_rule_binds_every_agent(
    client: AsyncClient, project: dict[str, Any], argo: Any
) -> None:
    await _connector(client)
    rule = {"properties": {"revision": {"pattern": "^v[0-9]+$"}}}
    bound = await client.patch(f"{ORG}/connectors/argo/operations/sync_application", json={"arguments": rule})
    assert bound.status_code == 200 and bound.json()["arguments"] == rule, bound.text
    base, headers = await _agent(client, project, ["sync_*"])  # a plain grant: no constraint of its own

    assert (await _call(client, base, headers, SYNC, {"name": "shop", "revision": "v3"})).status_code == 200
    _refused_outside(
        await _call(client, base, headers, SYNC, {"name": "shop", "revision": "main"}),
        "organisation",
        "/revision",
    )
    cleared = await client.patch(
        f"{ORG}/connectors/argo/operations/sync_application", json={"arguments": None}
    )
    assert cleared.status_code == 200 and cleared.json()["arguments"] is None
    assert (await _call(client, base, headers, SYNC, {"name": "shop", "revision": "main"})).status_code == 200


async def test_a_call_outside_a_grant_proposes_no_action(
    client: AsyncClient, project: dict[str, Any], argo: Any
) -> None:
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await _connector(client)
    base, headers = await _agent(client, project, [{"name": "delete_application", "arguments": ONLY_SHOP}])

    _refused_outside(
        await _call(client, base, headers, "argo__delete_application", {"name": "billing"}), "agent", "/name"
    )
    async with session_scope() as session:
        assert (await session.execute(select(Action.id))).all() == [], "nothing was proposed"
    proposed = await _call(client, base, headers, "argo__delete_application", {"name": "shop"})
    assert proposed.json()["status_code"] == 202, proposed.text


async def test_the_announced_schema_carries_the_bound(
    client: AsyncClient, project: dict[str, Any], argo: Any
) -> None:
    await _connector(client)
    base, headers = await _agent(
        client, project, [{"name": "sync_application", "arguments": ONLY_SHOP}, "delete_application"]
    )
    tools = {tool["name"]: tool for tool in (await client.get(base, headers=headers)).json()["tools"]}
    assert tools[SYNC]["inputSchema"] == {"type": "object", "allOf": [ARGO_SCHEMA, ONLY_SHOP]}
    assert tools["argo__delete_application"]["inputSchema"] == ARGO_SCHEMA, (
        "a plain grant is announced as before"
    )


@pytest.mark.parametrize(
    ("schema", "reason"),
    [
        ({"properties": {"name": {"$ref": "#/$defs/n"}}, "$defs": {"n": {"type": "string"}}}, "$ref"),
        ({"properties": {"name": {"type": 12}}}, "not a valid JSON Schema"),
        ({"properties": {"namespace": {"const": "x"}}}, "no argument `namespace`"),
        ({"required": ["namespace"]}, "no argument `namespace`"),
        ({"properties": {"name": {"const": "x" * 9000}}}, "KiB"),
    ],
)
async def test_a_constraint_that_cannot_be_read_is_refused_when_it_is_written(
    client: AsyncClient, project: dict[str, Any], argo: Any, schema: dict[str, Any], reason: str
) -> None:
    await _connector(client)
    rule = await client.patch(
        f"{ORG}/connectors/argo/operations/sync_application", json={"arguments": schema}
    )
    assert rule.status_code == 422 and reason in rule.text, rule.text
    narrowing = await client.put(
        f"/api/v1/projects/{project['id']}/operations/argo/sync_application",
        json={"policy": "allowed", "arguments": schema},
    )
    assert narrowing.status_code == 422 and reason in narrowing.text, narrowing.text
    spec = {
        "mcp_servers": [{"connector": "argo", "tools": [{"name": "sync_application", "arguments": schema}]}]
    }
    agent = await client.post(f"{ORG}/agents", json={"slug": "deployer", "display_name": "D", "spec": spec})
    assert agent.status_code == 422 and reason in agent.text, agent.text


async def test_a_grant_that_binds_nothing_or_twice_is_refused(
    client: AsyncClient, project: dict[str, Any], argo: Any
) -> None:
    await _connector(client)

    async def create(slug: str, tools: list[Any]) -> Any:
        spec = {"mcp_servers": [{"connector": "argo", "tools": tools}]}
        return await client.post(f"{ORG}/agents", json={"slug": slug, "display_name": slug, "spec": spec})

    nothing = await create("nothing-bound", [{"name": "destroy_*", "arguments": ONLY_SHOP}])
    assert nothing.status_code == 422 and "matches no operation" in nothing.text, nothing.text
    twice = await create(
        "twice-bound",
        [{"name": "sync_*", "arguments": ONLY_SHOP}, {"name": "sync_application", "arguments": ONLY_SHOP}],
    )
    assert twice.status_code == 422 and "already bound" in twice.text, twice.text
    assert (await create("once-bound", [{"name": "sync_*", "arguments": ONLY_SHOP}])).status_code == 201


async def test_a_version_that_keeps_plain_strings_keeps_its_digest() -> None:
    import hashlib
    import json

    from choregos_api.schemas import AgentSpec
    from choregos_api.services.agents import digest

    spec = AgentSpec.model_validate(
        {"mcp_servers": [{"connector": "argo", "tools": ["sync_*", "get_application"]}]}
    )
    content = spec.model_dump(mode="json")
    assert content["mcp_servers"] == [{"connector": "argo", "tools": ["sync_*", "get_application"]}]
    # What a version published before the field has always hashed to: the strings, as written.
    expected = {k: v for k, v in content.items() if k not in {"knowledge", "memory", "runtime", "network"}}
    blob = json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert digest(spec) == "sha256:" + hashlib.sha256(blob.encode()).hexdigest()
