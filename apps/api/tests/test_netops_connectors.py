"""The netops connectors in the organisation (S33-08, ADR 0052): pfSense, Cilium and nginx are
declared like any connector — their keys as references —, their operations are born as READS,
allowed, and a run reaches them through the broker as MCP tools: the key reaches the device,
never the pod.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"
#: Built at run time: no key-shaped literal for the secret scanner to argue about.
PFSENSE_KEY = "-".join(("pfsense", "key", "of", "the", "org"))
READER_TOKEN = "-".join(("cilium", "reader", "token", "of", "the", "org"))

CONNECTORS: dict[str, dict[str, Any]] = {
    "pfsense": {"type": "pfsense", "config": {"base_url": "https://fw.example.test"},
                "secret_refs": {"api_key": "env:NETOPS_TEST_PFSENSE_KEY"}},
    "cilium": {"type": "cilium", "config": {"kubernetes_url": "https://k8s.example.test"},
               "secret_refs": {"token": "env:NETOPS_TEST_READER_TOKEN"}},
    "nginx": {"type": "nginx", "config": {"repo": "acme/gitops"}},
}  # fmt: skip


@pytest.fixture
def estate(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    import choregos_adapters
    from choregos_adapters.fakes.netops import FakeCiliumKubernetes, FakePfSense, FakeRepo, recorded_flows

    fakes = {
        "pfsense": FakePfSense(api_key=PFSENSE_KEY),
        "kubernetes": FakeCiliumKubernetes(token=READER_TOKEN),
        "flows": recorded_flows(),
        "repo": FakeRepo(),
    }
    monkeypatch.setattr(choregos_adapters, "FAKE_NETOPS", fakes)
    monkeypatch.setenv("NETOPS_TEST_PFSENSE_KEY", PFSENSE_KEY)
    monkeypatch.setenv("NETOPS_TEST_READER_TOKEN", READER_TOKEN)
    yield fakes


async def _declare(client: AsyncClient) -> dict[str, dict[str, str]]:
    policies = {}
    for name, body in CONNECTORS.items():
        created = await client.post(f"{ORG}/connectors", json={"name": name, **body})
        assert created.status_code == 201, created.text
        assert PFSENSE_KEY not in created.text and READER_TOKEN not in created.text
        policies[name] = {o["name"]: o["policy"] for o in created.json()["operations"]}
    return policies


async def _run(client: AsyncClient, project: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """The catalogue's `netops` agent, installed as is, and a run of it."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token
    from choregos_core.catalogue_d_agents import entree

    netops = entree("netops")
    assert netops is not None
    created = await client.post(f"{ORG}/agents", json=netops.document)
    assert created.status_code == 201, created.text
    async with session_scope() as session:
        item = WorkItem(
            project_id=project["id"], tracker_key="varga/net#1", title="api unreachable", state="ready"
        )
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="run-netops-1",
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="diagnose",
                transition_id="t-diagnose",
                status="running",
                agent_slug="netops",
                agent_version=1,
            )
        )
    token = mint_run_token(
        "run-netops-1", project_slug="billing-api", work_item_key="varga/net#1", ttl_minutes=30
    )
    return "/api/v1/internal/runs/run-netops-1/tools", {"Authorization": f"Bearer {token}"}


async def test_netops_connectors_are_born_with_read_operations_allowed(
    client: AsyncClient, admin: str, estate: dict[str, Any]
) -> None:
    assert await _declare(client) == {
        "pfsense": {
            "gateway_status": "allowed",
            "list_aliases": "allowed",
            "list_firewall_rules": "allowed",
            "list_interfaces": "allowed",
            "list_port_forwards": "allowed",
        },
        "cilium": {
            "get_flows": "allowed",
            "list_clusterwide_policies": "allowed",
            "list_network_policies": "allowed",
        },
        "nginx": {"list_nginx_files": "allowed", "read_nginx_file": "allowed"},
    }
    clear = await client.post(
        f"{ORG}/connectors",
        json={"name": "fw2", "type": "pfsense", "config": {"base_url": "x", "api_key": "k"}},
    )
    assert clear.status_code == 422 and "in clear" in clear.text


async def test_the_netops_agent_reads_the_estate_through_the_broker(
    client: AsyncClient, project: dict[str, Any], estate: dict[str, Any]
) -> None:
    await _declare(client)
    base, headers = await _run(client, project)
    tools = {t["name"]: t for t in (await client.get(base, headers=headers)).json()["tools"]}
    assert set(tools) == {
        "pfsense__list_firewall_rules",
        "pfsense__list_aliases",
        "pfsense__list_port_forwards",
        "pfsense__list_interfaces",
        "pfsense__gateway_status",
        "cilium__list_network_policies",
        "cilium__list_clusterwide_policies",
        "cilium__get_flows",
        "nginx__list_nginx_files",
        "nginx__read_nginx_file",
    }
    assert not any("approval" in t["description"] for t in tools.values()), "reads: direct calls"

    flows = await client.post(
        f"{base}/cilium__get_flows", headers=headers, json={"namespace": "shop", "verdict": "DROPPED"}
    )
    assert flows.status_code == 200 and flows.json()["status_code"] == 200, flows.text
    dropped = flows.json()["result"]["structuredContent"]
    assert dropped["dropped"] == 3 and dropped["flows"][0]["drop_reason"] == "POLICY_DENIED"

    rules = await client.post(
        f"{base}/pfsense__list_firewall_rules", headers=headers, json={"interface": "wan"}
    )
    assert rules.json()["status_code"] == 200, rules.text
    assert PFSENSE_KEY not in rules.text, "the key reaches the firewall, never the pod"
    assert estate["pfsense"].received == [("GET", "/firewall/rules")]

    nginx = await client.post(f"{base}/nginx__read_nginx_file", headers=headers, json={"path": "nginx.conf"})
    assert "worker_processes" in nginx.json()["result"]["structuredContent"]["content"]
    outside = await client.post(f"{base}/nginx__read_nginx_file", headers=headers, json={"path": "../x"})
    assert outside.json()["status_code"] == 422

    unknown_argument = await client.post(f"{base}/cilium__get_flows", headers=headers, json={"cluster": "x"})
    assert unknown_argument.status_code == 400, "the schema is applied before Hubble is asked"
    assert {method for method, _ in estate["kubernetes"].received} <= {"GET"}
