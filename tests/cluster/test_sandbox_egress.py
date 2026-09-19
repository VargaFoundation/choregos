"""S2-12 — le bac à sable d'un runner : ce qui sort, et ce qui ne sort pas.

Les manifests ne sont pas écrits pour le test : ils viennent de `render_project_manifests`,
le même code que le provisioning. Ce qui est vérifié ici est donc ce qui sera appliqué.

Une NetworkPolicy acceptée n'est pas une NetworkPolicy appliquée : sans CNI qui la fait
respecter, l'API la stocke et le trafic passe quand même. Le premier test le vérifie avant
tous les autres — un bac à sable qui ne retient rien doit se voir.
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
    """Provisionne le projet avec ses vrais manifests, puis le retire."""
    config = ProjectConfig(
        slug=SLUG, org="varga", repo=RepoConfig(url="https://github.com/varga/sandbox.git")
    )
    manifests = render_project_manifests(SLUG, config, load_preset("team"))
    # Le chart de la plateforme n'est pas déployé ici : on n'applique que ce qui décrit le
    # bac à sable, c'est-à-dire ce que cette story doit prouver.
    apply([manifests[name] for name in ("namespaces.yaml", "netpol.yaml")])
    yield
    kubectl("delete", "ns", RUNNERS, f"proj-{SLUG}-ci", "--ignore-not-found", check=False)


def test_le_cni_applique_vraiment_les_politiques() -> None:
    """Garde-fou : si le CNI ignore les NetworkPolicy, tout le reste est un faux positif."""
    logs = run_probe(
        RUNNERS,
        "probe-cni",
        "curl -s -m 8 -o /dev/null -w '%{http_code}' https://example.com || echo BLOQUE",
    )
    assert "BLOQUE" in logs, (
        "le trafic sortant est passé malgré `default-deny-egress` — le CNI de ce cluster "
        f"n'applique pas les NetworkPolicy (sortie : {logs!r})"
    )


def test_un_runner_ne_joint_pas_internet() -> None:
    """Un agent ne doit pas pouvoir exfiltrer ni tirer du code depuis n'importe où."""
    for cible in ("https://example.com", "https://pypi.org/simple/", "http://1.1.1.1"):
        logs = run_probe(
            RUNNERS,
            "probe-internet",
            f"curl -s -m 8 -o /dev/null -w '%{{http_code}}' {cible} || echo BLOQUE",
        )
        assert "BLOQUE" in logs, f"{cible} joignable depuis un runner : {logs!r}"


def test_le_dns_reste_ouvert() -> None:
    """Sans DNS, un runner ne joint pas non plus ce qu'il a le droit de joindre."""
    logs = run_probe(
        RUNNERS,
        "probe-dns",
        "nslookup kubernetes.default.svc.cluster.local >/dev/null 2>&1 && echo DNS_OK || echo DNS_KO",
    )
    assert "DNS_OK" in logs, logs


def test_la_plateforme_reste_joignable() -> None:
    """Le runner doit joindre l'API interne : c'est par là qu'il poste son résultat."""
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
        f"curl -s -m 8 -o /dev/null -w '%{{http_code}}' http://{ip}:8000/ || echo BLOQUE",
    )
    kubectl("-n", "choregos-system", "delete", "pod", "fake-api", "--ignore-not-found", check=False)
    assert "200" in logs, f"l'API interne doit rester joignable depuis un runner : {logs!r}"


def test_un_port_non_autorise_de_la_plateforme_reste_ferme() -> None:
    """La politique ouvre des ports nommés, pas un namespace entier."""
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
        f"curl -s -m 8 -o /dev/null -w '%{{http_code}}' http://{ip}:9999/ || echo BLOQUE",
    )
    kubectl("-n", "choregos-system", "delete", "pod", "fake-secret", "--ignore-not-found", check=False)
    assert "BLOQUE" in logs, f"un port non ouvert par la politique doit rester fermé : {logs!r}"


def test_le_namespace_du_projet_est_en_pod_security_restricted() -> None:
    labels = kubectl("get", "ns", RUNNERS, "-o", "jsonpath={.metadata.labels}")
    assert '"pod-security.kubernetes.io/enforce":"restricted"' in labels.replace(" ", ""), labels


def test_un_pod_privilegie_est_refuse_par_le_cluster() -> None:
    """`restricted` n'est pas une intention : l'API doit refuser le pod."""
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
    assert result.returncode != 0, "un pod privilégié a été accepté dans un namespace runners"
    assert "violate" in (result.stderr + result.stdout).lower(), result.stderr[:300]
