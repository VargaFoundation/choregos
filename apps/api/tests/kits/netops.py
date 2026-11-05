"""The netops connectors of the organisation, declared, and a run of the catalogue's `netops` agent.

Shared by the netops connector tests (S33-08) and the result-bound tests (#1374): what two test modules
share lives in `kits/` (S45-42).
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"
#: Built at run time: no key-shaped literal for the secret scanner to argue about.
PFSENSE_KEY = "-".join(("pfsense", "key", "of", "the", "org"))
READER_TOKEN = "-".join(("cilium", "reader", "token", "of", "the", "org"))

CONNECTORS: dict[str, dict[str, Any]] = {
    "pfsense": {"type": "pfsense", "config": {"base_url": "https://fw.example.test"},
                "secret_refs": {"api_key": "env:CONNECTOR_NETOPS_TEST_PFSENSE_KEY"}},
    "cilium": {"type": "cilium", "config": {"kubernetes_url": "https://k8s.example.test"},
               "secret_refs": {"token": "env:CONNECTOR_NETOPS_TEST_READER_TOKEN"}},
    "nginx": {"type": "nginx", "config": {"repo": "acme/gitops"}},
}  # fmt: skip


async def declare_netops_connectors(client: AsyncClient) -> dict[str, dict[str, str]]:
    policies = {}
    for name, body in CONNECTORS.items():
        created = await client.post(f"{ORG}/connectors", json={"name": name, **body})
        assert created.status_code == 201, created.text
        assert PFSENSE_KEY not in created.text and READER_TOKEN not in created.text
        policies[name] = {o["name"]: o["policy"] for o in created.json()["operations"]}
    return policies


async def netops_run(
    client: AsyncClient,
    project: dict[str, Any],
    *,
    connector: str | None = None,
    work_item_key: str = "varga/net#1",
) -> tuple[str, dict[str, str]]:
    """The catalogue's `netops` agent, installed as is (its selection extended to `connector` when
    given), and a run of it, on the work item `work_item_key`."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token
    from choregos_core.agent_catalogue import entry

    netops = entry("netops")
    assert netops is not None
    document = netops.document
    if connector:
        servers = [*document["spec"]["mcp_servers"], {"connector": connector, "tools": ["*"]}]
        document = {**document, "spec": {**document["spec"], "mcp_servers": servers}}
    created = await client.post(f"{ORG}/agents", json=document)
    assert created.status_code == 201, created.text
    async with session_scope() as session:
        item = WorkItem(
            project_id=project["id"], tracker_key=work_item_key, title="api unreachable", state="ready"
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
        "run-netops-1", project_slug="billing-api", work_item_key=work_item_key, ttl_minutes=30
    )
    return "/api/v1/internal/runs/run-netops-1/tools", {"Authorization": f"Bearer {token}"}
