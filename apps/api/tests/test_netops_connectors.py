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
                "secret_refs": {"api_key": "env:CONNECTOR_NETOPS_TEST_PFSENSE_KEY"}},
    "cilium": {"type": "cilium", "config": {"kubernetes_url": "https://k8s.example.test"},
               "secret_refs": {"token": "env:CONNECTOR_NETOPS_TEST_READER_TOKEN"}},
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
    monkeypatch.setenv("CONNECTOR_NETOPS_TEST_PFSENSE_KEY", PFSENSE_KEY)
    monkeypatch.setenv("CONNECTOR_NETOPS_TEST_READER_TOKEN", READER_TOKEN)
    yield fakes


async def _declare(client: AsyncClient) -> dict[str, dict[str, str]]:
    policies = {}
    for name, body in CONNECTORS.items():
        created = await client.post(f"{ORG}/connectors", json={"name": name, **body})
        assert created.status_code == 201, created.text
        assert PFSENSE_KEY not in created.text and READER_TOKEN not in created.text
        policies[name] = {o["name"]: o["policy"] for o in created.json()["operations"]}
    return policies


async def _run(
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
    from choregos_core.catalogue_d_agents import entree

    netops = entree("netops")
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
            "create_firewall_rule": "approval",
            "update_firewall_rule": "approval",
            "delete_firewall_rule": "approval",
            "restore_firewall_rule": "approval",
        },
        "cilium": {
            "get_flows": "allowed",
            "list_clusterwide_policies": "allowed",
            "list_network_policies": "allowed",
            "propose_network_policy": "approval",
        },
        "nginx": {
            "list_nginx_files": "allowed",
            "read_nginx_file": "allowed",
            "propose_nginx_config": "approval",
        },
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
        "pfsense__create_firewall_rule",
        "pfsense__update_firewall_rule",
        "pfsense__delete_firewall_rule",
        "cilium__propose_network_policy",
        "nginx__propose_nginx_config",
    }, "restore_firewall_rule is a compensation, not the agent's"
    writes = {name for name, t in tools.items() if "needs a human approval" in t["description"]}
    assert writes == {name for name in tools if name.endswith("e_firewall_rule") or "__propose_" in name}

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


# ───────────────────────────── writes (S33-09) ─────────────────────────────


async def _call_as_effect(action_id: str) -> dict[str, Any]:
    """What the `ActionWorkflow` does with the action's first effect — here, without Temporal."""
    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope
    from choregos_api.effets import ContexteEffet, effet

    async with session_scope() as session:
        action = await session.get(Action, action_id)
        assert action is not None
        project = await session.get(Project, action.project_id)
        assert project is not None
        result: dict[str, Any] = await effet("connector.call")(
            ContexteEffet(session, action, project), dict(action.effects[0]["with"])
        )
        return result


async def _approve(action_id: str) -> None:
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        action = await session.get(Action, action_id)
        assert action is not None
        action.status = "approved"


async def test_a_pfsense_write_is_approved_by_a_person_always(
    client: AsyncClient, project: dict[str, Any], estate: dict[str, Any]
) -> None:
    """Not even the organisation's administrator opens it to the policy; a row forced behind the API
    does not either: the broker still proposes an action that waits for a person."""
    from choregos_api.db.models import ConnectorOperation
    from choregos_api.db.session import session_scope
    from sqlalchemy import update

    await _declare(client)
    for operation in ("create_firewall_rule", "update_firewall_rule", "delete_firewall_rule"):
        opened = await client.patch(
            f"{ORG}/connectors/pfsense/operations/{operation}", json={"policy": "allowed"}
        )
        assert opened.status_code == 422 and "approved by a person" in opened.text
    closed = await client.patch(
        f"{ORG}/connectors/pfsense/operations/delete_firewall_rule", json={"policy": "forbidden"}
    )
    assert closed.status_code == 200

    async with session_scope() as session:
        await session.execute(
            update(ConnectorOperation)
            .where(ConnectorOperation.name == "update_firewall_rule")
            .values(policy="allowed")
        )
    base, headers = await _run(client, project)
    tools = {t["name"]: t for t in (await client.get(base, headers=headers)).json()["tools"]}
    assert "needs a human approval" in tools["pfsense__update_firewall_rule"]["description"]
    assert "pfsense__delete_firewall_rule" not in tools, "forbidden: it does not exist for the run"
    call = await client.post(
        f"{base}/pfsense__update_firewall_rule",
        headers=headers,
        json={"tracker": "1700000102", "changes": {"destination_port": "9443"}},
    )
    assert call.json()["status_code"] == 202, call.text
    assert call.json()["result"]["status"] == "pending_approval"
    assert estate["pfsense"].writes == 0


async def test_an_unapproved_pfsense_write_is_refused_and_carries_its_compensation(
    client: AsyncClient, project: dict[str, Any], estate: dict[str, Any]
) -> None:
    from choregos_api.effets import EffetRefuse

    await _declare(client)
    base, headers = await _run(client, project)
    before = estate["pfsense"].state()
    proposed = await client.post(
        f"{base}/pfsense__update_firewall_rule",
        headers=headers,
        json={"tracker": "1700000102", "changes": {"destination_port": "9443"}},
    )
    action_id = proposed.json()["result"]["action"]
    action = (await client.get(f"/api/v1/projects/{project['id']}/actions/{action_id}")).json()
    assert action["status"] == "pending_approval" and action["origin"] == "tool"
    assert action["effects"][0]["compensate"]["with"] == {
        "connector": "pfsense",
        "operation": "restore_firewall_rule",
        "arguments": {"snapshot": "{{ result.snapshot }}"},
    }, "the broker attaches what the type declares undoes it"

    with pytest.raises(EffetRefuse, match="not approved"):
        await _call_as_effect(action_id)
    assert estate["pfsense"].writes == 0 and estate["pfsense"].state() == before

    await _approve(action_id)
    done = await _call_as_effect(action_id)
    assert done["changed"] is True and estate["pfsense"].rules[1]["destination_port"] == "9443"
    assert '"destination_port": "8443"' in done["snapshot"], "the previous rule, kept for the compensation"


MANIFEST = (
    "apiVersion: cilium.io/v2\nkind: CiliumNetworkPolicy\nmetadata: {name: api-ingress, namespace: shop}\n"
    "spec:\n  endpointSelector: {matchLabels: {app: api}}\n  ingress:\n"
    "    - fromEndpoints: [{matchLabels: {app: gateway}}, {matchLabels: {app: frontend}}]\n"
    "      toPorts: [{ports: [{port: '8080', protocol: TCP}]}]\n"
)


async def test_a_cilium_policy_change_is_a_pull_request_that_rides_the_work_item_s_train(
    client: AsyncClient, project: dict[str, Any], estate: dict[str, Any]
) -> None:
    from choregos_adapters.fakes import FakeScm
    from choregos_adapters.tracker.github_events import parse_github_event
    from choregos_api.effets import EffetRefuse

    scm = FakeScm()
    estate["scm"] = scm
    await _declare(client)
    configured = await client.post(
        f"{ORG}/connectors",
        json={"name": "cilium-prod", "type": "cilium",
              "config": {"kubernetes_url": "https://k8s.example.test", "gitops_repo": "acme/gitops"}},
    )  # fmt: skip
    assert configured.status_code == 201, configured.text
    base, headers = await _run(client, project, connector="cilium-prod", work_item_key="acme/gitops#7")
    arguments = {"path": "shop/api-ingress.yaml", "manifest": MANIFEST, "title": "Admit frontend on api:8080",
                 "rationale": "frontend is dropped by api-ingress (POLICY_DENIED)"}  # fmt: skip
    proposed = await client.post(
        f"{base}/cilium-prod__propose_network_policy", headers=headers, json=arguments
    )
    assert proposed.json()["status_code"] == 202, proposed.text
    action_id = proposed.json()["result"]["action"]
    with pytest.raises(EffetRefuse, match="not approved"):
        await _call_as_effect(action_id)
    assert scm.prs == {}

    await _approve(action_id)
    opened = await _call_as_effect(action_id)
    assert opened["files"] == ["cilium/shop/api-ingress.yaml"] and opened["work_item"] == "acme/gitops#7"
    assert scm.files[("acme/gitops", opened["branch"])]["cilium/shop/api-ingress.yaml"] == MANIFEST
    (pr,) = scm.prs.values()
    assert not pr.merged and scm.merge_queue == [], "proposed, never merged by the platform"
    assert {method for method, _ in estate["kubernetes"].received} <= {"GET"}, (
        "nothing applied to the cluster"
    )
    again = await _call_as_effect(action_id)
    assert again["pr"] == opened["pr"] and len(scm.prs) == 1, "a replay opens nothing twice"

    # Merged, the pull request comes back to ITS work item — which boards the train its workflow takes.
    pull_request = {"number": opened["pr"], "merged": True, "head": {"ref": opened["branch"]}}
    merged = {"action": "closed", "repository": {"full_name": "acme/gitops"}, "pull_request": pull_request}
    (event,) = parse_github_event("pull_request", "d-1", merged)
    assert (event.type.value, event.work_item_key) == ("scm.pr.merged", "acme/gitops#7")


async def test_a_proposal_that_is_not_one_cilium_policy_or_leaves_its_root_is_refused(
    client: AsyncClient, project: dict[str, Any], estate: dict[str, Any]
) -> None:
    from choregos_adapters.fakes import FakeScm
    from choregos_api.effets import EffetRefuse

    estate["scm"] = FakeScm()
    await _declare(client)
    base, headers = await _run(client, project)
    deployment = "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: api}\n"
    for path, manifest, refusal in (
        ("shop/x.yaml", deployment, "only CiliumClusterwideNetworkPolicy, CiliumNetworkPolicy"),
        ("../../kube-system/x.yaml", MANIFEST, "outside the configuration root"),
    ):
        proposed = await client.post(
            f"{base}/cilium__propose_network_policy",
            headers=headers,
            json={"path": path, "manifest": manifest, "title": "t", "rationale": "r"},
        )
        action_id = proposed.json()["result"]["action"]
        await _approve(action_id)
        with pytest.raises(EffetRefuse, match=refusal):
            await _call_as_effect(action_id)
    assert estate["scm"].prs == {}


async def test_an_nginx_change_is_a_pull_request_too(
    client: AsyncClient, project: dict[str, Any], estate: dict[str, Any]
) -> None:
    from choregos_adapters.fakes import FakeScm

    scm = FakeScm()
    estate["scm"] = scm
    await _declare(client)
    base, headers = await _run(client, project)
    proposed = await client.post(
        f"{base}/nginx__propose_nginx_config",
        headers=headers,
        json={
            "path": "conf.d/shop.conf",
            "content": "server { listen 8443 ssl; }",
            "title": "t",
            "rationale": "r",
        },
    )
    action_id = proposed.json()["result"]["action"]
    await _approve(action_id)
    opened = await _call_as_effect(action_id)
    assert opened["files"] == ["nginx/conf.d/shop.conf"]
    assert opened["branch"].startswith("netops/"), "a work item of another repository: no train to ride"
    assert estate["repo"].files["nginx/conf.d/shop.conf"].startswith("upstream"), (
        "the merged file is untouched"
    )
