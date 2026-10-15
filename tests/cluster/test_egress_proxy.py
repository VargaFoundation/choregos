"""A project's egress proxy, as provisioning writes it, on a real cluster.

The manifests come from `render_project_manifests`: what is checked is what will be applied. An
agent pod goes out through the proxy (`HTTPS_PROXY`): a domain on the allowlist answers, another
gets 403 from the proxy — without depending on the CNI, it is Squid that refuses.
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
        # curl ignores upper-case `HTTP_PROXY` (CGI legacy): both cases, as the runner
        # receives them (`env_du_runner`).
        f"export HTTPS_PROXY={PROXY} HTTP_PROXY={PROXY} https_proxy={PROXY} http_proxy={PROXY}; "
        f"curl -s -m 20 -o /dev/null -w '%{{http_code}}' {cible} || echo KO",
    )


def test_un_domaine_de_l_allowlist_passe() -> None:
    assert _via_proxy("https://api.github.com/").strip() == "200"


def test_un_domaine_hors_allowlist_est_refuse_par_le_proxy() -> None:
    # A refused CONNECT tunnel: curl returns 000 then "KO", or 403 depending on the version.
    tunnel = _via_proxy("https://example.com/").strip()
    assert tunnel.startswith("403") or tunnel.endswith("KO"), tunnel
    assert _via_proxy("http://example.com/").strip() == "403"
