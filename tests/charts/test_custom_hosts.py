"""Custom hosts (`global.customHosts`, ADR 0175, S74-04): routing, TLS and the guards around them.

A customer CNAMEs `agentic.client.com` to the instance. The chart must route that host to the console
and the API exactly as it routes the canonical one, ask cert-manager for its certificate, and leave
every guard where it was. Each test below fails without the template or the line it names.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"
HOST = "agentic.client.com"
NAME = "choregos-host-agentic-client-com"
ISSUER = "global.customHosts.certificate.issuerRef.name=choregos-issuer"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _run(*overrides: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART)]
    for override in overrides:
        command += ["--set", override]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _documents(*overrides: str) -> list[dict[str, Any]]:
    rendered = _run(*overrides)
    assert rendered.returncode == 0, rendered.stderr
    return [doc for doc in yaml.safe_load_all(rendered.stdout) if doc]


def _hosts(kind: str, *more: str, hosts: str = HOST) -> list[dict[str, Any]]:
    return _documents(
        f"global.customHosts.hosts={{{hosts}}}", f"global.customHosts.kind={kind}", ISSUER, *more
    )


def _one(documents: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    found = [d for d in documents if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(found) == 1, (kind, name, [(d["kind"], d["metadata"]["name"]) for d in documents])
    return found[0]


def _paths(ingress: dict[str, Any]) -> list[tuple[str, str, str]]:
    """The paths of the Ingress's first rule (the canonical one has a rule per hostname, all alike)."""
    return [
        (p["path"], p["pathType"], p["backend"]["service"]["name"])
        for p in ingress["spec"]["rules"][0]["http"]["paths"]
    ]


def _public_hosts(documents: list[dict[str, Any]]) -> list[str]:
    deployment = _one(documents, "Deployment", "choregos-api")
    env = {e["name"]: e.get("value") for e in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
    return list(json.loads(env["CHOREGOS_PUBLIC_HOSTS"]))


def _canonical_api_paths() -> list[tuple[str, str, str]]:
    ingress = _one(_documents("choregos-api.ingress.kind=ingress"), "Ingress", "choregos-api")
    return _paths(ingress)


def test_off_by_default_renders_nothing_and_changes_no_guard() -> None:
    documents = _documents()
    assert not [d for d in documents if d["metadata"]["name"].startswith("choregos-host-")]
    assert _public_hosts(documents) == ["api.choregos.local", "hooks.choregos.local"]


def test_a_plain_ingress_routes_the_console_and_the_api_like_the_canonical_host() -> None:
    documents = _hosts("ingress", "global.customHosts.className=nginx")
    ingress = _one(documents, "Ingress", NAME)
    assert ingress["spec"]["ingressClassName"] == "nginx"
    assert ingress["spec"]["rules"][0]["host"] == HOST
    # Exactly the API's own paths (same order, same types), then the console for the rest.
    assert _paths(ingress) == [*_canonical_api_paths(), ("/", "Prefix", "choregos-web")]
    assert ingress["spec"]["tls"] == [{"hosts": [HOST], "secretName": f"{NAME}-tls"}]


def test_the_certificate_asks_the_configured_issuer_for_the_host_alone() -> None:
    documents = _hosts("ingress")
    certificate = _one(documents, "Certificate", NAME)
    assert certificate["spec"]["dnsNames"] == [HOST]
    assert certificate["spec"]["issuerRef"] == {
        "name": "choregos-issuer",
        "kind": "Issuer",
        "group": "cert-manager.io",
    }
    # The secret the Certificate fills is the one the Ingress terminates TLS with.
    assert (
        certificate["spec"]["secretName"] == _one(documents, "Ingress", NAME)["spec"]["tls"][0]["secretName"]
    )


def test_nginx_mergeable_renders_a_master_with_the_tls_and_two_minions() -> None:
    documents = _hosts("nginx-mergeable", "global.customHosts.className=nginx")
    master = _one(documents, "Ingress", NAME)
    assert master["metadata"]["annotations"]["nginx.org/mergeable-ingress-type"] == "master"
    assert master["spec"]["tls"] == [{"hosts": [HOST], "secretName": f"{NAME}-tls"}]
    # A master holds the host and no path: NGINX Inc. rejects one that routes.
    assert master["spec"]["rules"] == [{"host": HOST}]
    api = _one(documents, "Ingress", f"{NAME}-api")
    web = _one(documents, "Ingress", f"{NAME}-web")
    for minion in (api, web):
        assert minion["metadata"]["annotations"]["nginx.org/mergeable-ingress-type"] == "minion"
        assert minion["spec"]["ingressClassName"] == "nginx"
        assert minion["spec"]["rules"][0]["host"] == HOST
        assert "tls" not in minion["spec"]
    assert _paths(api) == _canonical_api_paths()
    assert _paths(web) == [("/", "Prefix", "choregos-web")]
    assert _one(documents, "Certificate", NAME)["spec"]["secretName"] == f"{NAME}-tls"


def test_an_operator_cannot_turn_the_master_into_something_else() -> None:
    documents = _hosts(
        "nginx-mergeable",
        r"global.customHosts.annotations.nginx\.org/mergeable-ingress-type=minion",
        r"global.customHosts.annotations.nginx\.org/proxy-read-timeout=60s",
    )
    annotations = _one(documents, "Ingress", NAME)["metadata"]["annotations"]
    assert annotations["nginx.org/mergeable-ingress-type"] == "master"
    assert annotations["nginx.org/proxy-read-timeout"] == "60s"


def test_an_http_route_matches_what_the_canonical_route_matches() -> None:
    documents = _hosts(
        "httproute", "global.customHosts.gateway=edge", "global.customHosts.gatewayNamespace=gw"
    )
    route = _one(documents, "HTTPRoute", NAME)
    canonical = _one(_documents(), "HTTPRoute", "choregos-api")
    assert route["spec"]["hostnames"] == [HOST]
    assert route["spec"]["parentRefs"] == [{"name": "edge", "namespace": "gw"}]
    assert route["spec"]["rules"][0]["matches"] == canonical["spec"]["rules"][0]["matches"]
    assert route["spec"]["rules"][0]["backendRefs"][0]["name"] == "choregos-api"
    assert route["spec"]["rules"][1]["backendRefs"][0]["name"] == "choregos-web"
    assert not [d for d in documents if d["kind"] == "Ingress"]


def test_the_gateway_defaults_to_the_canonical_hosts() -> None:
    route = _one(_hosts("httproute"), "HTTPRoute", NAME)
    assert route["spec"]["parentRefs"] == [{"name": "choregos-gateway", "namespace": "gateway"}]


def test_the_hosts_are_public_names_for_the_api_guard() -> None:
    """Routed is public, proved or not: the run's API stays closed on the host from the first request."""
    assert _public_hosts(_hosts("ingress")) == ["api.choregos.local", "hooks.choregos.local", HOST]


def test_each_host_gets_its_own_objects() -> None:
    documents = _hosts("ingress", hosts=f"{HOST},data.other.org")
    assert _one(documents, "Ingress", NAME)
    assert _one(documents, "Ingress", "choregos-host-data-other-org")
    assert _one(documents, "Certificate", "choregos-host-data-other-org")["spec"]["dnsNames"] == [
        "data.other.org"
    ]


def test_without_a_certificate_the_operator_brings_the_secret() -> None:
    documents = _documents(
        f"global.customHosts.hosts={{{HOST}}}", "global.customHosts.certificate.enabled=false"
    )
    assert not [d for d in documents if d["kind"] == "Certificate"]
    assert _one(documents, "Ingress", NAME)["spec"]["tls"][0]["secretName"] == f"{NAME}-tls"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (["global.customHosts.hosts={*.client.com}", ISSUER], "not a bare lower-case DNS name"),
        ([f"global.customHosts.hosts={{https://{HOST}}}", ISSUER], "not a bare lower-case DNS name"),
        ([f"global.customHosts.hosts={{{HOST}:8443}}", ISSUER], "not a bare lower-case DNS name"),
        (["global.customHosts.hosts={Agentic.Client.com}", ISSUER], "not a bare lower-case DNS name"),
        (["global.customHosts.hosts={localhost}", ISSUER], "not a bare lower-case DNS name"),
        (["global.customHosts.hosts={api.choregos.local}", ISSUER], "is already routed by global.apiUrl"),
        (["global.customHosts.hosts={app.choregos.local}", ISSUER], "is already routed by global.publicUrl"),
        (
            ["global.customHosts.hosts={hooks.choregos.local}", ISSUER],
            "is already routed by choregos-api.ingress.hostnames",
        ),
        (
            [
                "global.customHosts.hosts={runs.client.com}",
                ISSUER,
                "choregos-api.runDoor.hostnames={runs.client.com}",
            ],
            "choregos-api.runDoor.hostnames",
        ),
        ([f"global.customHosts.hosts={{{HOST}}}"], "issuerRef.name is empty"),
        (
            [f"global.customHosts.hosts={{{HOST}}}", ISSUER, "global.customHosts.kind=virtualserver"],
            "not ingress",
        ),
    ],
)
def test_the_render_refuses_what_would_weaken_or_break_the_routing(
    overrides: list[str], message: str
) -> None:
    rendered = _run(*overrides)
    assert rendered.returncode != 0
    assert message in rendered.stderr, rendered.stderr


def test_two_hosts_with_one_object_name_are_refused() -> None:
    rendered = _run("global.customHosts.hosts={a.b-c.com,a-b.c.com}", ISSUER)
    assert rendered.returncode != 0
    assert "same object name" in rendered.stderr, rendered.stderr
