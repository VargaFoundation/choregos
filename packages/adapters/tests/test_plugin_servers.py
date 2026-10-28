"""The objects that run a plugin's server, and how they are applied (ADR 0096, S43-06)."""

from __future__ import annotations

import json

import httpx
import pytest
from choregos_adapters.fakes.kubernetes import FakeKubernetesApi
from choregos_adapters.plugin_servers import (
    CONFIG_DIGEST,
    PRIVATE_RANGES,
    PluginServerClient,
    PluginServerRuntime,
    ServerDeployment,
    server_manifests,
)

IMAGE = "ghcr.io/vargafoundation/choregos-agents/hello-mcp@sha256:" + "a" * 64


def _server(**changes: object) -> ServerDeployment:
    base: dict[str, object] = {
        "name": "hello-0123abcd",
        "namespace": "choregos-plugins",
        "image": IMAGE,
        "port": 8080,
        "health_path": "/healthz",
        "env": {"CHOREGOS_SERVER_TOKEN": "t" * 43, "BASE_URL": "https://greet.example.com"},
        "labels": {"app.kubernetes.io/name": "choregos-plugin-server"},
        "egress": (("greet.example.com", 443),),
    }
    base.update(changes)
    return ServerDeployment(**base)  # type: ignore[arg-type]


def _kind(objects: list[dict[str, object]], kind: str) -> dict[str, object]:
    return next(o for o in objects if o["kind"] == kind)


def test_default_egress_is_public_addresses_on_the_configured_ports() -> None:
    policy = _kind(server_manifests(_server()), "NetworkPolicy")["spec"]
    dns, internet = policy["egress"]  # type: ignore[index]
    assert dns["ports"][0]["port"] == 53
    assert internet["to"] == [{"ipBlock": {"cidr": "0.0.0.0/0", "except": list(PRIVATE_RANGES)}}]
    assert "169.254.0.0/16" in PRIVATE_RANGES, "never the cloud's metadata endpoint"
    assert internet["ports"] == [{"protocol": "TCP", "port": 443}]


def test_cilium_names_the_exact_hosts_and_the_network_policy_keeps_dns_only() -> None:
    objects = server_manifests(_server(egress_mode="cilium"))
    egress = _kind(objects, "NetworkPolicy")["spec"]["egress"]  # type: ignore[index]
    assert len(egress) == 1 and egress[0]["ports"][0]["port"] == 53
    cilium = _kind(objects, "CiliumNetworkPolicy")["spec"]["egress"]  # type: ignore[index]
    assert cilium[1]["toFQDNs"] == [{"matchName": "greet.example.com"}]


def test_no_host_no_egress_at_all() -> None:
    assert _kind(server_manifests(_server(egress=())), "NetworkPolicy")["spec"]["egress"] == []  # type: ignore[index]


def test_the_configuration_digest_rolls_the_pods() -> None:
    first = _kind(server_manifests(_server()), "Deployment")
    second = _kind(server_manifests(_server(env={"CHOREGOS_SERVER_TOKEN": "x"})), "Deployment")
    digest = [d["spec"]["template"]["metadata"]["annotations"][CONFIG_DIGEST] for d in (first, second)]  # type: ignore[index]
    assert digest[0] != digest[1]


async def test_apply_is_idempotent_and_delete_removes_everything() -> None:
    api = FakeKubernetesApi()
    runtime = PluginServerRuntime(api, "choregos-plugins")
    await runtime.apply(_server())
    created = len(api.calls)
    await runtime.apply(_server())
    assert [c for c in api.calls[created:] if c[0] != "GET"] == [], "replayed: nothing written"
    await runtime.apply(_server(replicas=0))
    assert api.store("deployments")["hello-0123abcd"]["spec"]["replicas"] == 0
    await runtime.delete("hello-0123abcd")
    assert not any(api.store(r) for r in ("deployments", "services", "networkpolicies", "secrets"))


async def test_the_server_client_presents_its_token_and_needs_no_egress_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The platform's call to its own Service is not an egress of the organisation: it passes in
    `enforce` with no policy in context, where any guarded client fails closed."""
    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "enforce")
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        if "id" not in body:
            return httpx.Response(202)
        result = {"tools": []} if body["method"] == "tools/list" else {"protocolVersion": "2025-06-18"}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    client = PluginServerClient(
        "http://hello-0123abcd.choregos-plugins.svc:8080/mcp",
        token="secret",
        transport=httpx.MockTransport(handle),
    )
    assert await client.list_tools() == []
    assert seen and all(r.headers["authorization"] == "Bearer secret" for r in seen)
