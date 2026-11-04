"""A project's egress proxy: delivered by provisioning, not promised by an annotation.

The inventory of 2026-09-24: the runners' NetworkPolicy opened a `choregos-egress` namespace that
no chart deployed, and the per-domain allowlist was only an annotation. These tests read the
manifests that provisioning writes: a Squid in the runners' namespace, its configuration drawn
from the policy, the network policy that opens the Internet only to it, and the environment of an
agent pod that names it.
"""

from __future__ import annotations

from types import SimpleNamespace

import yaml
from choregos_contracts import ProjectConfig, RepoConfig
from choregos_core import load_preset
from choregos_orchestrator.activities.execution import runner_environment
from choregos_orchestrator.gitops import EGRESS_NAME, KUBE_NS, render_project_manifests, squid_config


def _config() -> ProjectConfig:
    return ProjectConfig(
        slug="billing", org="varga", repo=RepoConfig(url="https://github.com/varga/billing.git")
    )


def _docs(text: str) -> list[dict]:
    return list(yaml.safe_load_all(text))


def test_the_proxy_is_rendered_with_the_policys_allowlist() -> None:
    files = render_project_manifests("billing", _config(), load_preset("team"))
    assert "egress.yaml" in files
    docs = {(d["kind"], d["metadata"]["name"]): d for d in _docs(files["egress.yaml"])}
    conf = docs[("ConfigMap", EGRESS_NAME)]["data"]["squid.conf"]
    for domain in load_preset("team").sandbox.network.allow_domains:
        assert f".{domain}" in conf, domain
    assert "http_access deny all" in conf
    assert "http_access deny CONNECT !SSL_ports" in conf
    assert "egress.yaml" in _docs(files["kustomization.yaml"])[0]["resources"]


def test_the_proxy_runs_without_root_without_capabilities_read_only() -> None:
    files = render_project_manifests("billing", _config(), load_preset("team"))
    deployment = next(d for d in _docs(files["egress.yaml"]) if d["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["automountServiceAccountToken"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert container["command"][0] == "squid", "without the image's entrypoint, which wants root"


def test_only_the_proxy_goes_out_to_the_internet() -> None:
    files = render_project_manifests("billing", _config(), load_preset("team"))
    netpol = {d["metadata"]["name"]: d for d in _docs(files["netpol.yaml"])}
    rules = netpol["allow-platform-egress"]["spec"]["egress"]
    destinations = [to for rule in rules for to in rule["to"]]
    assert {"podSelector": {"matchLabels": {"app.kubernetes.io/name": EGRESS_NAME}}} in destinations
    namespaces = [to.get("namespaceSelector", {}).get("matchLabels", {}).get(KUBE_NS) for to in destinations]
    assert "choregos-egress" not in namespaces, "the phantom namespace is no longer promised"
    outbound = next(d for d in _docs(files["egress.yaml"]) if d["kind"] == "NetworkPolicy")
    assert outbound["spec"]["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": EGRESS_NAME}}
    block = outbound["spec"]["egress"][0]["to"][0]["ipBlock"]
    assert block["cidr"] == "0.0.0.0/0" and "10.0.0.0/8" in block["except"]


def test_without_an_allowed_domain_there_is_no_proxy_and_no_door() -> None:
    files = render_project_manifests("billing", _config(), load_preset("regulated"))
    assert "egress.yaml" not in files
    assert "egress.yaml" not in _docs(files["kustomization.yaml"])[0]["resources"]
    netpol = {d["metadata"]["name"]: d for d in _docs(files["netpol.yaml"])}
    destinations = [to for rule in netpol["allow-platform-egress"]["spec"]["egress"] for to in rule["to"]]
    assert not any("podSelector" in to for to in destinations)


def test_the_configuration_normalises_the_domains() -> None:
    conf = squid_config(["github.com", ".pypi.org", "github.com"])
    assert "acl allowed dstdomain .github.com .pypi.org" in conf


def test_the_agent_pod_receives_the_proxy_of_its_namespace() -> None:
    settings = SimpleNamespace(
        runner_env={"TZ": "UTC"}, runner_egress_proxy="http://choregos-egress.{namespace}.svc:3128"
    )
    env = runner_environment(settings, "proj-billing-runners")
    assert env["HTTPS_PROXY"] == "http://choregos-egress.proj-billing-runners.svc:3128"
    assert env["https_proxy"] == env["HTTPS_PROXY"]
    assert ".svc" in env["NO_PROXY"] and "127.0.0.1" in env["NO_PROXY"]
    assert env["TZ"] == "UTC"


def test_without_a_configured_proxy_the_environment_stays_the_deployments() -> None:
    env = runner_environment(SimpleNamespace(runner_env={"TZ": "UTC"}, runner_egress_proxy=""), "choregos")
    assert env == {"TZ": "UTC"}


def test_the_internal_callback_never_goes_through_the_egress_proxy() -> None:
    """`NO_PROXY` must name the hosts that the PLATFORM gives the runner, not a suffix.

    The `.svc` suffix covers `choregos-api.choregos.svc` and **not** `choregos-api` — yet that is
    the short form the platform builds itself (`callback_url`). On the dev tenant on 2026-09-27,
    the runner's first call (fetching its `StageInput`) therefore went to the egress proxy, which
    did not exist in that namespace: "Name or service not known", without the message naming the
    URL or the proxy. The runner concluded "StageInput not found".

    The proxy exists to control what GOES OUT. The internal callback does not go out.
    """
    settings = SimpleNamespace(
        runner_env={},
        runner_egress_proxy="http://choregos-egress.{namespace}.svc:3128",
        callback_url="http://choregos-api:8000",
        gateway_url="http://litellm:4000",
        memory_url="http://ecphoria:8432",
    )
    without_proxy = runner_environment(settings, "choregos")["NO_PROXY"].split(",")
    for host in ("choregos-api", "litellm", "ecphoria"):
        assert host in without_proxy, f"{host} would go through the egress proxy ({without_proxy})"


def test_the_direct_hosts_come_from_the_platforms_urls() -> None:
    """No name is guessed: a change of URL follows, without touching this list."""
    from choregos_orchestrator.activities.execution import hosts_to_reach_directly

    hosts = hosts_to_reach_directly(
        SimpleNamespace(
            # A host WITHOUT a dot or a port stuck to "api": `api.x:8443/api/v1/...` triggers the
            # generic gitleaks rule, which sees a key in it. The test is about extracting a
            # host, not about the shape of the name.
            callback_url="https://internal-example:8443/api/v1/internal",
            gateway_url="http://gateway:4000",
            memory_url="",
        )
    )
    assert hosts == ["internal-example", "gateway"], hosts


def test_the_pod_asks_the_proxy_for_credentials_only_if_the_deployment_requires_it() -> None:
    """ADR 0070: `CHOREGOS_EGRESS_AUTH=run` tells the runner to present its run id and token."""
    proxy = "http://choregos-egress.{namespace}.svc:3128"
    base = {"runner_env": {"TZ": "UTC"}, "runner_egress_proxy": proxy}
    with_auth = runner_environment(SimpleNamespace(**base, runner_egress_auth="run"), "choregos")
    assert with_auth["CHOREGOS_EGRESS_AUTH"] == "run"
    assert with_auth["HTTPS_PROXY"] == "http://choregos-egress.choregos.svc:3128", "no credentials here"
    for settings in (SimpleNamespace(**base, runner_egress_auth=""), SimpleNamespace(**base)):
        assert "CHOREGOS_EGRESS_AUTH" not in runner_environment(settings, "choregos")
