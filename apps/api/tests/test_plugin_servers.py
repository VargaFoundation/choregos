"""A plugin's server, run and governed by the platform (S43-06, ADR 0096).

Each guard has its test: installing runs the server (Deployment, Service, NetworkPolicy, Secret) under
the pod rules; the connector is named after the server and carries one operation per declared one at
its floor; `human_approval` is never lowered; a tool the server does not declare is refused by
discovery, by the broker and by a governed action; the bearer token reaches the server on every call;
the configuration is the server's schema, sealed for its secrets, and rolls the Deployment; the image
rules (allowlist, signature); a name collision is 409; a reconcile replayed changes nothing; uninstall
deletes everything.
"""

from __future__ import annotations

import base64
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from choregos_adapters.fakes.kubernetes import FakeKubernetesApi
from choregos_adapters.plugin_servers import CONFIG_DIGEST, TOKEN_ENV, PluginServerRuntime
from httpx import AsyncClient
from sqlalchemy import func, select

from .agent_scopes_kit import add_member, as_user, make_project

ORG = "/api/v1/orgs/varga"
HELLO = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugins" / "hello"
INSTALL = {"source": "offline", "name": "hello", "version": "1.0.0"}
SECOND = "second-admin@varga.dev"


@pytest.fixture
def kube(tmp_path: Path) -> Iterator[FakeKubernetesApi]:
    """The offline source serves `hello` 1.0.0 (one server); servers run in an in-memory cluster."""
    from choregos_api.services.plugin_servers import use_plugin_runtime, use_plugin_transport
    from choregos_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    root = tmp_path / "plugins"
    shutil.copytree(HELLO, root / "hello" / "1.0.0")
    use_plugin_supply(OfflineDirectory(root))
    api = FakeKubernetesApi()
    use_plugin_runtime(PluginServerRuntime(api, "choregos-plugins"))
    yield api
    use_plugin_runtime(None)
    use_plugin_transport(None)
    use_plugin_supply(None)


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    from choregos_api.config import reset_settings_cache

    yield monkeypatch
    reset_settings_cache()


def _set(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    from choregos_api.config import reset_settings_cache

    for name, value in env.items():
        monkeypatch.setenv(name, value)
    reset_settings_cache()


async def _ask(client: AsyncClient) -> httpx.Response:
    return await client.post(f"{ORG}/plugins/install", json=INSTALL)


async def _install(client: AsyncClient) -> None:
    asked = await _ask(client)
    assert asked.status_code == 202, asked.text
    await add_member(client, SECOND, "org_admin")
    await as_user(client, SECOND)
    approved = await client.post(f"{ORG}/plugin-approvals/{asked.json()['approval_id']}/approve")
    assert approved.status_code == 200, approved.text
    await as_user(client, "admin@varga.dev")


async def _connector() -> Any:
    from choregos_api.db.models import OrgConnector
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return (await session.execute(select(OrgConnector).where(OrgConnector.name == "hello"))).scalar_one()


def _only(api: FakeKubernetesApi, resource: str) -> dict[str, Any]:
    objects = list(api.store(resource).values())
    assert len(objects) == 1, objects
    return objects[0]


def _env(api: FakeKubernetesApi) -> dict[str, str]:
    data = _only(api, "secrets")["data"]
    return {k: base64.b64decode(v).decode() for k, v in data.items()}


async def test_install_runs_the_server_under_the_pod_rules(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi, settings_env: pytest.MonkeyPatch
) -> None:
    _set(
        settings_env,
        CHOREGOS_PLUGIN_SERVERS_RUNTIME_CLASS="gvisor",
        CHOREGOS_PLUGIN_SERVERS_API_NAMESPACE="choregos",
    )
    await _install(client)
    deployment = _only(kube, "deployments")
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert container["image"].startswith("ghcr.io/vargafoundation/choregos-agents/hello-mcp@sha256:")
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["runtimeClassName"] == "gvisor"
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert container["resources"]["limits"] == {"cpu": "500m", "memory": "512Mi"}
    assert container["readinessProbe"]["httpGet"]["path"] == "/healthz"
    assert deployment["spec"]["replicas"] == 1
    service = _only(kube, "services")
    assert service["spec"]["ports"][0]["port"] == 8080
    policy = _only(kube, "networkpolicies")["spec"]
    assert policy["policyTypes"] == ["Ingress", "Egress"]
    (rule,) = policy["ingress"]
    assert rule["from"] == [
        {
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "choregos-api"}},
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "choregos"}},
        }
    ]
    assert policy["egress"] == [], "no configured host: the server reaches nothing"
    assert len(_env(kube)[TOKEN_ENV]) >= 32


async def test_the_connector_is_named_after_the_server_with_its_operations_at_their_floor(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi
) -> None:
    await _install(client)
    listed = (await client.get(f"{ORG}/connectors/hello")).json()
    assert listed["kind"] == "mcp" and listed["status"] == "deployed"
    policies = {op["name"]: (op["access"], op["policy"]) for op in listed["operations"]}
    assert policies == {
        "say_hello": ("read", "allowed"),
        "post_greeting": ("write", "approval"),
        "set_greeting": ("write", "approval"),
        "delete_greeting": ("write", "approval"),
    }
    # The platform's token is sealed (ADR 0058): said set, never shown, and no route reaches the field.
    (token,) = [state for state in listed["secrets"] if state["field"] == "_server_token"]
    assert token["source"] == "store" and "value" not in token
    assert (
        await client.put(f"{ORG}/connectors/hello/secrets/_server_token", json={"value": "x"})
    ).status_code == 422


async def test_human_approval_is_never_lowered(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi
) -> None:
    from choregos_api.db.models import ConnectorOperation
    from choregos_api.db.session import session_scope
    from choregos_api.services.connecteurs import human_floor

    await _install(client)
    refused = await client.patch(
        f"{ORG}/connectors/hello/operations/post_greeting", json={"policy": "allowed"}
    )
    assert refused.status_code == 422, refused.text
    # A write the server does not mark `human_approval` is the organisation's to open.
    opened = await client.patch(f"{ORG}/connectors/hello/operations/set_greeting", json={"policy": "allowed"})
    assert opened.status_code == 200, opened.text
    async with session_scope() as session:
        row = (
            await session.execute(
                select(ConnectorOperation).where(ConnectorOperation.name == "post_greeting")
            )
        ).scalar_one()
        row.policy = "allowed"  # behind the API
    instance = await _connector()
    assert human_floor(instance, "post_greeting", "allowed") == "approval"
    assert human_floor(instance, "set_greeting", "allowed") == "allowed"


async def test_the_declaration_carries_compensation_and_platform_arguments(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi
) -> None:
    from choregos_api.services.connecteurs import declared_operation

    await _install(client)
    declared = declared_operation(await _connector(), "post_greeting")
    assert declared is not None and declared.human_approval
    assert declared.compensation == {
        "operation": "delete_greeting",
        "arguments": {"greeting_id": "{{ result.id }}"},
    }
    assert declared.platform_arguments == ("action_key",)


def _mcp_server(tools: list[str], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        method = body.get("method")
        if method == "initialize":
            result: dict[str, Any] = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "hello"}}
        elif method == "tools/list":
            result = {"tools": [{"name": t, "inputSchema": {"type": "object"}} for t in tools]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "hi"}]}
        else:
            return httpx.Response(202)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    return httpx.MockTransport(handle)


async def test_an_undeclared_tool_is_refused_and_the_token_rides_every_call(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi
) -> None:
    from choregos_api.db.models import ConnectorOperation, OrgConnector, Project
    from choregos_api.db.session import session_scope
    from choregos_api.services.connecteurs import politique_pour_le_projet
    from choregos_api.services.courtier import tools_of_selection
    from choregos_api.services.plugin_servers import use_plugin_transport

    await _install(client)
    seen: list[httpx.Request] = []
    use_plugin_transport(_mcp_server(["say_hello", "post_greeting", "rogue"], seen))
    found = await client.post(f"{ORG}/connectors/hello/discover")
    assert found.status_code == 200, found.text
    assert "rogue" not in found.json()["added"]
    names = {op["name"] for op in (await client.get(f"{ORG}/connectors/hello")).json()["operations"]}
    assert "rogue" not in names and "set_greeting" in names, "declared operations stay, undeclared never come"
    token = _env(kube)[TOKEN_ENV]
    assert seen and all(r.headers["authorization"] == f"Bearer {token}" for r in seen)
    for request in seen:  # the Service the platform wrote, never an address an administrator typed
        assert str(request.url).startswith("http://hello-")
        assert str(request.url).endswith(".choregos-plugins.svc:8080/mcp")

    await make_project(client, "greet")
    async with session_scope() as session:
        connector = (
            await session.execute(select(OrgConnector).where(OrgConnector.name == "hello"))
        ).scalar_one()
        # A row written behind the API, wide open: still refused, the declaration is the floor.
        session.add(
            ConnectorOperation(
                org_id=connector.org_id,
                connector_id=connector.id,
                name="rogue",
                access="read",
                policy="allowed",
                groups=[],
            )
        )
        await session.flush()
        project = (await session.execute(select(Project).where(Project.slug == "greet"))).scalar_one()
        tools = await tools_of_selection(session, {"hello": ["*"]}, project)
        assert "hello__say_hello" in tools and "hello__rogue" not in tools
        assert tools["hello__post_greeting"].sous_validation
        assert await politique_pour_le_projet(session, project, "hello", "rogue") == "forbidden"


async def test_configuration_is_the_server_s_schema_and_rolls_the_deployment(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi
) -> None:
    await _install(client)
    before = _only(kube, "deployments")["spec"]["template"]["metadata"]["annotations"][CONFIG_DIGEST]
    wrong = await client.patch(f"{ORG}/connectors/hello", json={"config": {"greeting": 5}})
    assert wrong.status_code == 422, wrong.text
    clear = await client.patch(f"{ORG}/connectors/hello", json={"config": {"token": "in clear"}})
    assert clear.status_code == 422, "a secret field is never written in clear"
    changed = await client.patch(
        f"{ORG}/connectors/hello",
        json={
            "config": {"greeting": "hi", "base_url": "https://greetings.example.com"},
            "secret_values": {"token": "s3cr3t-value"},
        },
    )
    assert changed.status_code == 200, changed.text
    assert "s3cr3t-value" not in changed.text
    env = _env(kube)
    assert env["GREETING"] == "hi" and env["BASE_URL"] == "https://greetings.example.com"
    assert env["TOKEN"] == "s3cr3t-value"
    after = _only(kube, "deployments")["spec"]["template"]["metadata"]["annotations"][CONFIG_DIGEST]
    assert after != before, "a configuration change rolls the pods"
    egress = _only(kube, "networkpolicies")["spec"]["egress"]
    assert {"protocol": "TCP", "port": 443} in egress[1]["ports"]


async def test_the_connector_answers_the_server_s_schema_for_the_console(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi
) -> None:
    """S43-08: the console configures a server with its own schema, not the `mcp` type's (`url`)."""
    await _install(client)
    listed = (await client.get(f"{ORG}/connectors/hello")).json()
    assert listed["server"] == {
        "plugin": "hello",
        "version": "1.0.0",
        "server": "hello",
        "config_schema": {
            "type": "object",
            "properties": {"base_url": {"type": "string", "format": "uri"}, "greeting": {"type": "string"}},
        },
        "secret_fields": ["token"],
    }
    created = await client.post(
        f"{ORG}/connectors",
        json={"name": "plain", "type": "mcp", "config": {"url": "https://mcp.example.com/mcp"}},
    )
    assert created.status_code == 201, created.text
    assert created.json()["server"] is None, "an ordinary MCP connector is no plugin's server"


async def test_a_host_the_bounds_deny_is_refused_at_configuration(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi, settings_env: pytest.MonkeyPatch
) -> None:
    await _install(client)
    _set(settings_env, CHOREGOS_NETWORK_INSTANCE_DENY="evil.example")
    refused = await client.patch(
        f"{ORG}/connectors/hello", json={"config": {"base_url": "https://evil.example"}}
    )
    assert refused.status_code == 422, refused.text
    assert "evil.example" in refused.text


async def test_a_name_collision_is_409_before_anything_is_written(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi
) -> None:
    from choregos_api.db.models import OrgPluginApproval
    from choregos_api.db.session import session_scope

    made = await client.post(
        f"{ORG}/connectors",
        json={"name": "hello", "type": "mcp", "config": {"url": "https://mcp.example.com/mcp"}},
    )
    assert made.status_code == 201, made.text
    asked = await _ask(client)
    assert asked.status_code == 409, asked.text
    async with session_scope() as session:
        assert (await session.execute(select(func.count()).select_from(OrgPluginApproval))).scalar_one() == 0
    assert not kube.objects.get("deployments")


@pytest.mark.parametrize(
    ("env", "needle"),
    [
        ({"CHOREGOS_PLUGIN_SERVERS_REGISTRIES": '["ghcr.io/acme"]'}, "not from a registry"),
        ({"CHOREGOS_HOSTED_COSIGN": "enforce"}, "no trusted signature"),
    ],
)
async def test_the_image_rules_refuse(
    client: AsyncClient,
    admin: str,
    kube: FakeKubernetesApi,
    settings_env: pytest.MonkeyPatch,
    env: dict[str, str],
    needle: str,
) -> None:
    from choregos_adapters.fakes import FakeImageVerifier, FakeRegistryClient
    from choregos_api.services.hosted_images import set_image_services

    set_image_services(FakeRegistryClient(), FakeImageVerifier())
    try:
        _set(settings_env, **env)
        refused = await _ask(client)
    finally:
        set_image_services(None)
    assert refused.status_code == 422, refused.text
    assert needle in refused.text


async def test_a_signed_image_passes_enforce(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi, settings_env: pytest.MonkeyPatch
) -> None:
    import yaml
    from choregos_adapters.fakes import FakeImageVerifier, FakeRegistryClient
    from choregos_api.services.hosted_images import set_image_services

    verifier = FakeImageVerifier()
    verifier.sign(yaml.safe_load((HELLO / "servers" / "hello.yaml").read_text())["image"])
    set_image_services(FakeRegistryClient(), verifier)
    try:
        _set(settings_env, CHOREGOS_HOSTED_COSIGN="enforce")
        assert (await _ask(client)).status_code == 202
    finally:
        set_image_services(None)


async def test_a_deployment_without_plugin_servers_refuses_one(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.services import plugin_servers

    plugin_servers.use_plugin_runtime(None)
    monkeypatch.setattr(plugin_servers, "fakes_enabled", lambda: False)
    refused = await _ask(client)
    assert refused.status_code == 422 and "runs none" in refused.text


async def test_a_reconcile_replayed_changes_nothing(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi
) -> None:
    import copy

    from choregos_api.db.session import session_scope
    from choregos_api.services.plugin_servers import reconcile

    await _install(client)
    before, calls = copy.deepcopy(kube.objects), len(kube.calls)
    async with session_scope() as session:
        from choregos_api.db.models import OrgConnector

        instance = (
            await session.execute(select(OrgConnector).where(OrgConnector.name == "hello"))
        ).scalar_one()
        assert await reconcile(session, instance) == "deployed"
        assert await reconcile(session, instance) == "deployed"
    assert kube.objects == before
    assert not [c for c in kube.calls[calls:] if c[0] == "POST"], "nothing created twice"


async def test_uninstall_deletes_the_server_and_its_connector(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi
) -> None:
    from choregos_api.db.models import ConnectorOperation, OrgConnector
    from choregos_api.db.session import session_scope
    from choregos_api.secret_store import get_secret_store

    await _install(client)
    instance = await _connector()
    kept = await client.delete(f"{ORG}/connectors/hello")
    assert kept.status_code == 409, "a plugin's server goes with its plugin, not through the connector route"
    gone = await client.delete(f"{ORG}/plugins/hello")
    assert gone.status_code == 200, gone.text
    for resource in ("deployments", "services", "networkpolicies", "secrets"):
        assert not kube.store(resource), resource
    async with session_scope() as session:
        for model in (OrgConnector, ConnectorOperation):
            assert (await session.execute(select(func.count()).select_from(model))).scalar_one() == 0, model
    sealed = getattr(get_secret_store(), "values", {})
    assert not [n for o, n in sealed if o == instance.org_id and n.startswith("connector/hello/")], (
        "the token too"
    )


async def test_the_platform_fills_its_arguments_and_drops_the_agent_s(
    client: AsyncClient, admin: str, kube: FakeKubernetesApi
) -> None:
    """`platform_arguments` (ADR 0052, declared by the server since ADR 0096): an agent that writes an
    `action_key` of its own does not choose the idempotency key the server trusts."""
    from types import SimpleNamespace

    from choregos_api.effets import _arguments_de_la_plateforme

    await _install(client)
    ctx: Any = SimpleNamespace(
        session=None, action=SimpleNamespace(id="act-1", work_item_id=None), project=None
    )
    arguments = {"text": "hi", "action_key": "forged"}
    filled = await _arguments_de_la_plateforme(ctx, await _connector(), "post_greeting", arguments)
    assert arguments == {"text": "hi"}
    assert filled["action_key"] != "forged" and len(filled["action_key"]) == 24
    assert await _arguments_de_la_plateforme(ctx, await _connector(), "say_hello", {"action_key": "x"}) == {}
