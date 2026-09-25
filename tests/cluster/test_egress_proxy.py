"""Le proxy d'egress d'un projet, tel que le provisioning l'écrit, sur un vrai cluster.

Les manifests viennent de `render_project_manifests` : ce qui est vérifié est ce qui sera
appliqué. Un pod d'agent sort par le proxy (`HTTPS_PROXY`) : un domaine de l'allowlist
répond, un autre reçoit 403 du proxy — sans dépendre du CNI, c'est Squid qui refuse.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from choregos_contracts import ProjectConfig, RepoConfig
from choregos_core import load_preset
from choregos_orchestrator.gitops import EGRESS_NAME, EGRESS_PORT, render_project_manifests

from .conftest import apply, kubectl, run_probe

pytestmark = pytest.mark.cluster

SLUG = "egress-test"
RUNNERS = f"proj-{SLUG}-runners"
PROXY = f"http://{EGRESS_NAME}.{RUNNERS}.svc:{EGRESS_PORT}"


@pytest.fixture(scope="module", autouse=True)
def project() -> Iterator[None]:
    config = ProjectConfig(slug=SLUG, org="varga", repo=RepoConfig(url="https://github.com/varga/egress.git"))
    manifests = render_project_manifests(SLUG, config, load_preset("team"))
    apply([manifests[name] for name in ("namespaces.yaml", "egress.yaml")])
    kubectl("-n", RUNNERS, "rollout", "status", f"deploy/{EGRESS_NAME}", "--timeout=180s", timeout_s=240)
    yield
    kubectl("delete", "ns", RUNNERS, f"proj-{SLUG}-ci", "--ignore-not-found", check=False)


def _via_proxy(cible: str) -> str:
    return run_probe(
        RUNNERS,
        "probe-proxy",
        # curl ignore `HTTP_PROXY` en majuscules (héritage CGI) : les deux casses, comme
        # le runner les reçoit (`env_du_runner`).
        f"export HTTPS_PROXY={PROXY} HTTP_PROXY={PROXY} https_proxy={PROXY} http_proxy={PROXY}; "
        f"curl -s -m 20 -o /dev/null -w '%{{http_code}}' {cible} || echo KO",
    )


def test_un_domaine_de_l_allowlist_passe() -> None:
    assert _via_proxy("https://api.github.com/").strip() == "200"


def test_un_domaine_hors_allowlist_est_refuse_par_le_proxy() -> None:
    # Un tunnel CONNECT refusé : curl rend 000 puis « KO », ou 403 selon la version.
    tunnel = _via_proxy("https://example.com/").strip()
    assert tunnel.startswith("403") or tunnel.endswith("KO"), tunnel
    assert _via_proxy("http://example.com/").strip() == "403"
