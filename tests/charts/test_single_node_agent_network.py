"""S80-07c, ADR 0184: on the single-node profile, an agent pod reaches nothing it should not.

On one k3s machine the agents run in the release namespace, beside the embedded PostgreSQL, Temporal,
LiteLLM and Ecphoria. The profile renders the chart's NetworkPolicies for that layout and the per-run
egress proxy. These tests evaluate the RENDERED policies the way a NetworkPolicy implementation
(k3s's kube-router) does, for pods carrying the labels the executors really put on agent pods
(`k8s_job.RUNNER_POD_LABELS`, the SandboxTemplate's pod labels) and the labels the chart really puts on
its own pods: an agent reaches DNS, the API, the model gateway, the memory and the egress proxy, and
never PostgreSQL, Temporal or the outside directly; the platform's pods still reach what they use.
"""

from __future__ import annotations

import ipaddress
import pathlib
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

import pytest
import yaml
from tranibor_adapters.executor.k8s_job import RUNNER_POD_LABELS

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
PROFILE = CHART / "values" / "single-node.yaml"
NAMESPACE = "choregos"
OPERATOR_API = "extensions.agents.x-k8s.io/v1beta1/SandboxTemplate"
SITE = [
    "--set",
    "backup.target.s3.url=s3://backups/choregos",
    "--set",
    "backup.target.s3.endpoint=http://s3:8333",
]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = [
        "helm",
        "template",
        "choregos",
        str(CHART),
        "-n",
        NAMESPACE,
        "-f",
        str(PROFILE),
        *SITE,
        *arguments,
    ]
    return subprocess.run(command, capture_output=True, text=True, check=check, timeout=120)


def _documents(*arguments: str) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(_render(*arguments).stdout) if d]


# ------------------------------------------------------------------ a NetworkPolicy evaluator


@dataclass(frozen=True)
class Pod:
    name: str
    labels: dict[str, str]
    ip: str
    namespace: str = NAMESPACE


@dataclass(frozen=True)
class Outside:
    """An address outside the cluster's pods: the Internet, the LAN, the node."""

    ip: str
    namespace: None = None
    labels: dict[str, str] = field(default_factory=dict)


Endpoint = Pod | Outside


def _selects(selector: dict[str, Any] | None, labels: dict[str, str]) -> bool:
    selector = selector or {}
    for key, value in (selector.get("matchLabels") or {}).items():
        if labels.get(key) != value:
            return False
    for expression in selector.get("matchExpressions") or []:
        key, operator, values = expression["key"], expression["operator"], expression.get("values") or []
        if operator == "In" and labels.get(key) not in values:
            return False
        if operator == "NotIn" and labels.get(key) in values:
            return False
        if operator == "Exists" and key not in labels:
            return False
        if operator == "DoesNotExist" and key in labels:
            return False
    return True


def _in_block(ip: str, block: dict[str, Any]) -> bool:
    address = ipaddress.ip_address(ip)
    if address not in ipaddress.ip_network(block["cidr"]):
        return False
    return not any(address in ipaddress.ip_network(cidr) for cidr in block.get("except") or [])


def _peer_matches(peer: dict[str, Any], policy_namespace: str, other: Endpoint) -> bool:
    if "ipBlock" in peer:
        # Conservative: an ipBlock is taken to match pod addresses too (implementations differ).
        return _in_block(other.ip, peer["ipBlock"])
    if other.namespace is None:
        return False
    if "namespaceSelector" in peer:
        namespace_labels = {"kubernetes.io/metadata.name": other.namespace}
        if not _selects(peer["namespaceSelector"], namespace_labels):
            return False
    elif other.namespace != policy_namespace:
        return False
    return "podSelector" not in peer or _selects(peer["podSelector"], other.labels)


def _port_matches(rule: dict[str, Any], port: int, protocol: str) -> bool:
    ports = rule.get("ports")
    if not ports:
        return True
    for entry in ports:
        if entry.get("protocol", "TCP") != protocol:
            continue
        if "port" not in entry:
            return True
        start = entry["port"]
        if isinstance(start, int) and start <= port <= entry.get("endPort", start):
            return True
    return False


def _allowed(
    policies: list[dict[str, Any]], pod: Pod, other: Endpoint, port: int, protocol: str, direction: str
) -> bool:
    """Whether `direction` ("Egress" from `pod`, "Ingress" to `pod`) admits the traffic with `other`."""
    key, peers = ("egress", "to") if direction == "Egress" else ("ingress", "from")
    selecting = [
        p
        for p in policies
        if p["metadata"].get("namespace", NAMESPACE) == pod.namespace
        and direction in p["spec"].get("policyTypes", [])
        and _selects(p["spec"].get("podSelector"), pod.labels)
    ]
    if not selecting:
        return True
    for policy in selecting:
        for rule in policy["spec"].get(key) or []:
            if not _port_matches(rule, port, protocol):
                continue
            if not rule.get(peers) or any(_peer_matches(peer, pod.namespace, other) for peer in rule[peers]):
                return True
    return False


def _reaches(
    policies: list[dict[str, Any]], source: Pod, target: Endpoint, port: int, protocol: str = "TCP"
) -> bool:
    if not _allowed(policies, source, target, port, protocol, "Egress"):
        return False
    return not isinstance(target, Pod) or _allowed(policies, target, source, port, protocol, "Ingress")


# ------------------------------------------------------------------ the pods, from the render


def _template_labels(document: dict[str, Any]) -> dict[str, str]:
    spec = document["spec"]
    if document["kind"] == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    if document["kind"] == "SandboxTemplate":
        return dict(spec["podTemplate"]["metadata"]["labels"])
    return dict(spec["template"]["metadata"]["labels"])


@pytest.fixture(scope="module")
def rendered() -> list[dict[str, Any]]:
    return _documents()


@pytest.fixture(scope="module")
def policies(rendered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in rendered if d["kind"] == "NetworkPolicy"]


@pytest.fixture(scope="module")
def pods(rendered: list[dict[str, Any]]) -> dict[str, Pod]:
    wanted = {
        ("StatefulSet", "choregos-postgresql"): "postgresql",
        ("Deployment", "choregos-temporal"): "temporal",
        ("Deployment", "choregos-litellm"): "litellm",
        ("Deployment", "choregos-ecphoria"): "ecphoria",
        ("Deployment", "choregos-api"): "api",
        ("Deployment", "choregos-web"): "web",
        ("Deployment", "choregos-orchestrator-executor"): "orchestrator",
        ("Deployment", "choregos-egress"): "egress",
        ("CronJob", "choregos-backup"): "backup",
        ("CronJob", "choregos-restore"): "restore",
    }
    found: dict[str, Pod] = {}
    for document in rendered:
        role = wanted.get((document["kind"], document["metadata"]["name"]))
        if role:
            found[role] = Pod(role, _template_labels(document), f"10.42.0.{len(found) + 10}")
    assert set(found) == set(wanted.values()), set(wanted.values()) - set(found)
    found["dns"] = Pod("dns", {"k8s-app": "kube-dns"}, "10.42.0.2", namespace="kube-system")
    return found


@pytest.fixture(scope="module")
def sandbox_labels() -> dict[str, str]:
    """The pod labels of the `agent_sandbox` executor's runners, should the profile switch to it."""
    documents = _documents("--set", "global.runner.executor=agent_sandbox", "--api-versions", OPERATOR_API)
    template = next(d for d in documents if d["kind"] == "SandboxTemplate")
    return _template_labels(template)


@pytest.fixture(params=["k8s_job", "agent_sandbox"])
def agent(request: pytest.FixtureRequest, sandbox_labels: dict[str, str]) -> Pod:
    """An agent pod, with the labels its executor really puts on it."""
    if request.param == "k8s_job":
        labels = {"choregos/run-id": "run-1", **RUNNER_POD_LABELS}
    else:
        labels = sandbox_labels
    return Pod(f"agent ({request.param})", labels, "10.42.0.99")


# ------------------------------------------------------------------ what an agent reaches


def test_the_profile_renders_the_policies_and_the_egress_proxy(rendered: list[dict[str, Any]]) -> None:
    names = {(d["kind"], d["metadata"]["name"]) for d in rendered}
    for name in (
        "choregos-default-deny-egress",
        "choregos-allow-platform",
        "choregos-runner-egress",
        "choregos-egress-net",
        "choregos-postgresql-ingress",
        "choregos-temporal-ingress",
    ):
        assert ("NetworkPolicy", name) in names, name
    assert ("Deployment", "choregos-egress") in names
    assert ("Service", "choregos-egress") in names


def test_the_evaluator_is_not_vacuous(pods: dict[str, Pod], agent: Pod) -> None:
    """Without the policies, the agent reaches the database: what the profile had before S80-07c."""
    assert _reaches([], agent, pods["postgresql"], 5432)


@pytest.mark.parametrize(("store", "port"), [("postgresql", 5432), ("temporal", 7233), ("temporal", 8233)])
def test_an_agent_never_reaches_the_database_or_temporal(
    policies: list[dict[str, Any]], pods: dict[str, Pod], agent: Pod, store: str, port: int
) -> None:
    assert not _reaches(policies, agent, pods[store], port), f"{agent.name} reaches {store}:{port}"


def test_the_stores_refuse_an_agent_even_if_its_egress_were_open(
    policies: list[dict[str, Any]], pods: dict[str, Pod], agent: Pod
) -> None:
    """The second wall: the store's own ingress, checked alone."""
    for store, port in (("postgresql", 5432), ("temporal", 7233)):
        assert not _allowed(policies, pods[store], agent, port, "TCP", "Ingress"), store


@pytest.mark.parametrize(
    ("target", "port"), [("api", 8000), ("litellm", 4000), ("ecphoria", 8432), ("egress", 3128)]
)
def test_an_agent_reaches_what_a_run_uses(
    policies: list[dict[str, Any]], pods: dict[str, Pod], agent: Pod, target: str, port: int
) -> None:
    assert _reaches(policies, agent, pods[target], port), f"{agent.name} cannot reach {target}:{port}"


def test_an_agent_resolves_names(policies: list[dict[str, Any]], pods: dict[str, Pod], agent: Pod) -> None:
    assert _reaches(policies, agent, pods["dns"], 53, "UDP")
    assert _reaches(policies, agent, pods["dns"], 53, "TCP")


@pytest.mark.parametrize(
    ("ip", "port"),
    [("203.0.113.10", 443), ("203.0.113.10", 80), ("192.168.1.10", 5432), ("10.0.0.1", 6443)],
)
def test_an_agent_never_goes_out_directly(
    policies: list[dict[str, Any]], agent: Pod, ip: str, port: int
) -> None:
    """Out of the cluster only through the egress proxy, which asks the API what the run may reach."""
    assert not _reaches(policies, agent, Outside(ip), port)


def test_an_agent_does_not_reach_the_web_server_or_the_backup(
    policies: list[dict[str, Any]], pods: dict[str, Pod], agent: Pod
) -> None:
    assert not _reaches(policies, agent, pods["web"], 3000)
    assert not _reaches(policies, agent, pods["orchestrator"], 8080)


# ------------------------------------------------------------------ the platform still works


@pytest.mark.parametrize("client", ["api", "orchestrator", "litellm", "backup", "restore"])
def test_the_platform_s_pods_reach_the_database(
    policies: list[dict[str, Any]], pods: dict[str, Pod], client: str
) -> None:
    assert _reaches(policies, pods[client], pods["postgresql"], 5432)


@pytest.mark.parametrize("client", ["api", "orchestrator"])
def test_the_workers_and_the_api_reach_temporal(
    policies: list[dict[str, Any]], pods: dict[str, Pod], client: str
) -> None:
    assert _reaches(policies, pods[client], pods["temporal"], 7233)


def test_the_web_server_does_not_reach_the_database(
    policies: list[dict[str, Any]], pods: dict[str, Pod]
) -> None:
    assert not _reaches(policies, pods["web"], pods["postgresql"], 5432)


@pytest.mark.parametrize(
    ("client", "ip", "port"),
    [
        ("litellm", "203.0.113.10", 443),  # the model provider
        ("api", "203.0.113.10", 443),  # the OIDC provider, the trackers
        ("orchestrator", "172.18.0.2", 6443),  # the Kubernetes API, on the k3s node's address
        ("backup", "172.18.0.3", 8333),  # the backup bucket, on the LAN
    ],
)
def test_the_platform_s_pods_reach_the_site_s_services(
    policies: list[dict[str, Any]], pods: dict[str, Pod], client: str, ip: str, port: int
) -> None:
    assert _reaches(policies, pods[client], Outside(ip), port)


def test_the_egress_proxy_takes_only_agents_and_goes_out_to_public_addresses(
    policies: list[dict[str, Any]], pods: dict[str, Pod]
) -> None:
    assert not _reaches(policies, pods["api"], pods["egress"], 3128)
    assert _reaches(policies, pods["egress"], Outside("203.0.113.10"), 443)
    assert not _reaches(policies, pods["egress"], Outside("192.168.1.10"), 443)
    assert not _reaches(policies, pods["egress"], pods["postgresql"], 5432)


def test_the_runners_are_told_to_use_the_proxy(rendered: list[dict[str, Any]]) -> None:
    executor = next(
        d
        for d in rendered
        if d["kind"] == "Deployment" and d["metadata"]["name"] == "choregos-orchestrator-executor"
    )
    env = {e["name"]: e.get("value") for e in executor["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["CHOREGOS_RUNNER_EGRESS_PROXY"] == "http://choregos-egress.{namespace}.svc:3128"
    assert env["CHOREGOS_RUNNER_NAMESPACE_PATTERN"] == NAMESPACE
    assert env["CHOREGOS_RUNNER_EGRESS_AUTH"] == "run"


# ------------------------------------------------------------------ the guard


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("networkPolicies.enabled=false", "singleNode.enabled=true needs networkPolicies.enabled=true"),
        ("global.egress.enabled=false", "singleNode.enabled=true needs global.egress.enabled=true"),
    ],
)
def test_the_profile_refuses_to_render_without_the_confinement(value: str, message: str) -> None:
    result = _render("--set", value, check=False)
    assert result.returncode != 0
    assert message in result.stderr
