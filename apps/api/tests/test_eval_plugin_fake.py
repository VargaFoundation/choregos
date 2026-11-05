"""A plugin's server on its fake for the live eval matrix: its eval instance, bound by the matrix (#1372).

A server that declares its `fake` is run, where the deployment serves eval fakes
(`CHOREGOS_EVAL_FAKE_SCENES`), as a second instance: its image with the fake's variables and the
platform's token, nothing of the configuration, no egress. The server's own instance keeps serving the
organisation. A connector reaches the eval instance only when the matrix's seeding bound it
(`fake_scene` in its configuration): no edit may write the field, whoever asks, and without the switch
it is ignored. Here the servicenow fixture (mirrored from choregos-agents#8, its `fake` added) is
installed from the offline source, its servers in an in-memory cluster.
"""

from __future__ import annotations

import base64
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from choregos_adapters.fakes.kubernetes import FakeKubernetesApi
from choregos_adapters.fakes.plugin_servers import FakePluginServer
from choregos_adapters.fakes.scenes import EVAL_FAKE_SCENES_ENV, FAKE_SCENE_FIELD
from choregos_adapters.plugin_servers import TOKEN_ENV, PluginServerRuntime
from httpx import AsyncClient
from sqlalchemy import select

from .agent_scopes_kit import add_member, as_user

ORG = "/api/v1/orgs/varga"
FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
SECOND = "second-admin@varga.dev"


@pytest.fixture
def kube(tmp_path: Path) -> Iterator[FakeKubernetesApi]:
    """The offline source serves `servicenow` 0.1.1 and `hello` 1.0.0; servers run in memory."""
    from choregos_api.services.plugin_servers import use_plugin_runtime, use_plugin_transport
    from choregos_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    root = tmp_path / "plugins"
    shutil.copytree(FIXTURES / "plugins" / "servicenow", root / "servicenow" / "0.1.1")
    shutil.copytree(FIXTURES / "plugins" / "hello", root / "hello" / "1.0.0")
    use_plugin_supply(OfflineDirectory(root))
    api = FakeKubernetesApi()
    use_plugin_runtime(PluginServerRuntime(api, "choregos-plugins"))
    yield api
    use_plugin_runtime(None)
    use_plugin_transport(None)
    use_plugin_supply(None)


@pytest.fixture
def eval_fakes(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv(EVAL_FAKE_SCENES_ENV, "1")
    return monkeypatch


async def _install(client: AsyncClient, name: str = "servicenow", version: str = "0.1.1") -> None:
    asked = await client.post(
        f"{ORG}/plugins/install", json={"source": "offline", "name": name, "version": version}
    )
    assert asked.status_code == 202, asked.text
    await add_member(client, SECOND, "org_admin")
    await as_user(client, SECOND)
    approved = await client.post(f"{ORG}/plugin-approvals/{asked.json()['approval_id']}/approve")
    assert approved.status_code == 200, approved.text
    await as_user(client, "admin@varga.dev")


async def _connector(name: str = "servicenow") -> Any:
    from choregos_api.db.models import OrgConnector
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return (await session.execute(select(OrgConnector).where(OrgConnector.name == name))).scalar_one()


async def _bind(name: str = "servicenow", scene: str = "default") -> None:
    """What the matrix's seeding writes (`_case_plugin_server`): the binding, behind the API."""
    from choregos_api.db.models import OrgConnector
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        row = (await session.execute(select(OrgConnector).where(OrgConnector.name == name))).scalar_one()
        row.config = {FAKE_SCENE_FIELD: scene}


async def _reconcile(name: str = "servicenow") -> None:
    from choregos_api.db.models import OrgConnector
    from choregos_api.db.session import session_scope
    from choregos_api.services.plugin_servers import reconcile

    async with session_scope() as session:
        row = (await session.execute(select(OrgConnector).where(OrgConnector.name == name))).scalar_one()
        await reconcile(session, row)


def _objects(api: FakeKubernetesApi, resource: str) -> dict[str, dict[str, Any]]:
    return {obj["metadata"]["name"]: obj for obj in api.store(resource).values()}


def _env(secret: dict[str, Any]) -> dict[str, str]:
    return {k: base64.b64decode(v).decode() for k, v in secret["data"].items()}


async def test_a_server_with_a_fake_runs_its_eval_instance_where_the_deployment_serves_eval_fakes(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi, eval_fakes: pytest.MonkeyPatch
) -> None:
    await _install(client)
    server = (await _connector()).plugin_server
    real, fake = server["object"], server["fake_object"]
    assert (
        fake.startswith("servicenow-fake-")
        and server["fake_url"] == f"http://{fake}.choregos-plugins.svc:8080/mcp"
    )
    deployments = _objects(kube, "deployments")
    assert set(deployments) == {real, fake}
    assert deployments[real]["spec"]["replicas"] == 0, "the server's own instance: nobody configured it"
    assert deployments[fake]["spec"]["replicas"] == 1
    assert deployments[fake]["metadata"]["labels"]["choregos.dev/fake"] == "true"
    secrets = _objects(kube, "secrets")
    assert _env(secrets[fake]) == {"SERVICENOW_FAKE": "1", TOKEN_ENV: _env(secrets[real])[TOKEN_ENV]}, (
        "the fake's variables and the platform's token, nothing of the configuration"
    )
    assert _objects(kube, "networkpolicies")[fake]["spec"]["egress"] == [], "it reaches nothing"


async def test_without_the_switch_there_is_no_eval_instance_and_turning_it_off_deletes_it(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(EVAL_FAKE_SCENES_ENV, raising=False)
    await _install(client)
    server = (await _connector()).plugin_server
    assert set(_objects(kube, "deployments")) == {server["object"]}
    monkeypatch.setenv(EVAL_FAKE_SCENES_ENV, "1")
    await _reconcile()
    assert set(_objects(kube, "deployments")) == {server["object"], server["fake_object"]}
    monkeypatch.delenv(EVAL_FAKE_SCENES_ENV)
    await _reconcile()
    assert set(_objects(kube, "deployments")) == {server["object"]}
    assert set(_objects(kube, "secrets")) == {server["object"]}


async def test_a_server_without_a_fake_has_no_eval_instance(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi, eval_fakes: pytest.MonkeyPatch
) -> None:
    await _install(client, "hello", "1.0.0")
    server = (await _connector("hello")).plugin_server
    assert "fake_url" not in server and set(_objects(kube, "deployments")) == {server["object"]}


async def test_no_edit_binds_a_server_s_connector_to_its_eval_instance(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi, eval_fakes: pytest.MonkeyPatch
) -> None:
    """`hello`'s schema takes any field: the refusal is the platform's, not the schema's."""
    await _install(client, "hello", "1.0.0")
    for config in ({FAKE_SCENE_FIELD: "default"}, {"greeting": "hi", FAKE_SCENE_FIELD: "default"}):
        refused = await client.patch(f"{ORG}/connectors/hello", json={"config": config})
        assert refused.status_code == 422, refused.text
        assert "only the matrix's seeding writes it" in refused.text
    assert FAKE_SCENE_FIELD not in ((await _connector("hello")).config or {})
    accepted = await client.patch(f"{ORG}/connectors/hello", json={"config": {"greeting": "hi"}})
    assert accepted.status_code == 200, "an edit without the field is the schema's to judge"
    await _install(client)
    refused = await client.patch(
        f"{ORG}/connectors/servicenow", json={"config": {FAKE_SCENE_FIELD: "default"}}
    )
    assert refused.status_code == 422 and "only the matrix's seeding writes it" in refused.text


async def test_a_bound_connector_reaches_the_eval_instance_where_the_switch_is_on_and_only_there(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services.plugin_servers import connector_client, use_plugin_transport

    monkeypatch.setenv(EVAL_FAKE_SCENES_ENV, "1")
    await _install(client)
    server = (await _connector()).plugin_server
    hosts: list[str] = []
    recorded = FakePluginServer.from_recording(FIXTURES / "plugin_fakes" / "servicenow.json", token=None)
    inner = recorded.transport()

    async def handle(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return await inner.handle_async_request(request)

    use_plugin_transport(httpx.MockTransport(handle))

    async def call() -> dict[str, Any]:
        async with session_scope() as session:
            row = await _connector()
            found: dict[str, Any] = await (await connector_client(session, row)).call_tool(
                "get_record", {"table": "incident", "number": "INC0012345"}
            )
            return found

    hosts.clear()
    await call()
    assert set(hosts) == {f"{server['object']}.choregos-plugins.svc"}, "not bound: the server's own instance"
    await _bind()
    hosts.clear()
    answered = await call()
    assert set(hosts) == {f"{server['fake_object']}.choregos-plugins.svc"}, "bound: its eval instance"
    assert answered["structuredContent"]["fields"]["number"] == "INC0012345"
    await _bind(scene="no-such-scene")
    hosts.clear()
    await call()
    assert set(hosts) == {f"{server['object']}.choregos-plugins.svc"}, "a scene the fake does not serve"
    await _bind()
    monkeypatch.delenv(EVAL_FAKE_SCENES_ENV)
    hosts.clear()
    await call()
    assert set(hosts) == {f"{server['object']}.choregos-plugins.svc"}, "ignored without the switch"
