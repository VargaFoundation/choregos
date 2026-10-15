"""S2-12 — a runner's sandbox: what gets out, and what does not.

The manifests are not written for the test: they come from `render_project_manifests`, the
same code as provisioning. What is checked here is therefore what will be applied.

An accepted NetworkPolicy is not an enforced NetworkPolicy: without a CNI that enforces it, the
API stores it and the traffic goes through anyway. The first test checks this before all the
others — a sandbox that holds nothing back must be visible.
"""

from __future__ import annotations

import pytest
from choregos_contracts import ProjectConfig, RepoConfig
from choregos_core import load_preset
from choregos_orchestrator.gitops import render_project_manifests

from .conftest import apply, kubectl, run_probe

pytestmark = pytest.mark.cluster

SLUG = "sandbox-test"
RUNNERS = f"proj-{SLUG}-runners"


@pytest.fixture(scope="module", autouse=True)
def project() -> None:
    """Provision the project with its real manifests, then remove it."""
    config = ProjectConfig(
        slug=SLUG, org="varga", repo=RepoConfig(url="https://github.com/varga/sandbox.git")
    )
    manifests = render_project_manifests(SLUG, config, load_preset("team"))
    # The platform chart is not deployed here: only what describes the sandbox is applied,
    # which is what this story has to prove.
    apply([manifests[name] for name in ("namespaces.yaml", "netpol.yaml")])
    yield
    kubectl("delete", "ns", RUNNERS, f"proj-{SLUG}-ci", "--ignore-not-found", check=False)


def test_le_cni_applique_vraiment_les_politiques() -> None:
    """Safeguard: if the CNI ignores NetworkPolicy, everything else is a false positive."""
    logs = run_probe(
        RUNNERS,
        "probe-cni",
        "curl -s -m 8 -o /dev/null -w '%{http_code}' https://example.com || echo BLOCKED",
    )
    assert "BLOCKED" in logs, (
        "outbound traffic got through despite `default-deny-egress` — this cluster's CNI "
        f"does not enforce NetworkPolicy (output: {logs!r})"
    )


def test_un_runner_ne_joint_pas_internet() -> None:
    """An agent must not be able to exfiltrate or pull code from anywhere."""
    for cible in ("https://example.com", "https://pypi.org/simple/", "http://1.1.1.1"):
        logs = run_probe(
            RUNNERS,
            "probe-internet",
            f"curl -s -m 8 -o /dev/null -w '%{{http_code}}' {cible} || echo BLOCKED",
        )
        assert "BLOCKED" in logs, f"{cible} reachable from a runner: {logs!r}"


def test_le_dns_reste_ouvert() -> None:
    """Without DNS, a runner cannot reach what it is allowed to reach either."""
    logs = run_probe(
        RUNNERS,
        "probe-dns",
        "nslookup kubernetes.default.svc.cluster.local >/dev/null 2>&1 && echo DNS_OK || echo DNS_KO",
    )
    assert "DNS_OK" in logs, logs


def test_la_plateforme_reste_joignable() -> None:
    """The runner must reach the internal API: that is how it posts its result."""
    kubectl("create", "ns", "choregos-system", "--dry-run=client", "-o", "yaml")
    kubectl(
        "apply",
        "-f",
        "-",
        input_text=(
            "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: choregos-system\n"
            "  labels:\n    kubernetes.io/metadata.name: choregos-system\n"
        ),
    )
    kubectl(
        "apply",
        "-f",
        "-",
        input_text=(
            "apiVersion: v1\nkind: Pod\nmetadata:\n  name: fake-api\n  namespace: choregos-system\n"
            "  labels: { app: fake-api }\nspec:\n  containers:\n  - name: http\n"
            "    image: hashicorp/http-echo:1.0\n    args: ['-listen=:8000', '-text=ok']\n"
            "    ports: [{ containerPort: 8000 }]\n"
        ),
    )
    kubectl("-n", "choregos-system", "wait", "--for=condition=Ready", "pod/fake-api", "--timeout=120s")
    ip = kubectl("-n", "choregos-system", "get", "pod", "fake-api", "-o", "jsonpath={.status.podIP}").strip()

    logs = run_probe(
        RUNNERS,
        "probe-api",
        f"curl -s -m 8 -o /dev/null -w '%{{http_code}}' http://{ip}:8000/ || echo BLOCKED",
    )
    kubectl("-n", "choregos-system", "delete", "pod", "fake-api", "--ignore-not-found", check=False)
    assert "200" in logs, f"the internal API must stay reachable from a runner: {logs!r}"


def test_un_port_non_autorise_de_la_plateforme_reste_ferme() -> None:
    """The policy opens named ports, not a whole namespace."""
    kubectl(
        "apply",
        "-f",
        "-",
        input_text=(
            "apiVersion: v1\nkind: Pod\nmetadata:\n  name: fake-secret\n  namespace: choregos-system\n"
            "spec:\n  containers:\n  - name: http\n    image: hashicorp/http-echo:1.0\n"
            "    args: ['-listen=:9999', '-text=secret']\n    ports: [{ containerPort: 9999 }]\n"
        ),
    )
    kubectl("-n", "choregos-system", "wait", "--for=condition=Ready", "pod/fake-secret", "--timeout=120s")
    ip = kubectl(
        "-n", "choregos-system", "get", "pod", "fake-secret", "-o", "jsonpath={.status.podIP}"
    ).strip()

    logs = run_probe(
        RUNNERS,
        "probe-port",
        f"curl -s -m 8 -o /dev/null -w '%{{http_code}}' http://{ip}:9999/ || echo BLOCKED",
    )
    kubectl("-n", "choregos-system", "delete", "pod", "fake-secret", "--ignore-not-found", check=False)
    assert "BLOCKED" in logs, f"a port not opened by the policy must stay closed: {logs!r}"


def test_le_namespace_du_projet_est_en_pod_security_restricted() -> None:
    labels = kubectl("get", "ns", RUNNERS, "-o", "jsonpath={.metadata.labels}")
    assert '"pod-security.kubernetes.io/enforce":"restricted"' in labels.replace(" ", ""), labels


def test_un_pod_privilegie_est_refuse_par_le_cluster() -> None:
    """`restricted` is not an intention: the API must refuse the pod."""
    manifest = (
        "apiVersion: v1\nkind: Pod\nmetadata:\n  name: privilegie\n"
        f"  namespace: {RUNNERS}\nspec:\n  containers:\n  - name: root\n"
        "    image: busybox:1.36\n    command: ['sleep','5']\n"
        "    securityContext:\n      privileged: true\n"
    )
    import subprocess

    from .conftest import context

    result = subprocess.run(
        ["kubectl", "--context", context(), "apply", "-f", "-"],
        input=manifest,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0, "a privileged pod was accepted in a runners namespace"
    assert "violate" in (result.stderr + result.stdout).lower(), result.stderr[:300]
