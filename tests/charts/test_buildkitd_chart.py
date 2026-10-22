"""The chart's rootless `buildkitd` (S41-13, ADR 0080 §9): rendered only where a zip can be built
with it, reachable by the orchestrator alone, out to the Internet and never to the platform, and
the one pod whose profile is weaker than the strict rule, by name and for that rule only."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

from .test_securite_des_pods import manquements

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
KYVERNO = "kyverno.io/v2/PolicyException"
#: Passing `--api-versions` turns off the conftest's own, so the operator's CRD is named here too.
OPERATOR = "extensions.agents.x-k8s.io/v1beta1/SandboxTemplate"
BUILDS = "global.images.buildRegistry=registry.internal/choregos-builds"
PRIVATE = {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"}

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")

Docs = dict[tuple[str, str], dict[str, Any]]


def _docs(*overrides: str, api_versions: tuple[str, ...] = ()) -> Docs:
    command = ["helm", "template", "choregos", str(CHART), "-n", "choregos"]
    for override in overrides:
        command += ["--set", override]
    for version in (OPERATOR, *api_versions):
        command += ["--api-versions", version]
    rendered = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    assert rendered.returncode == 0, rendered.stderr
    return {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(rendered.stdout) if d}


def _workers(docs: Docs) -> list[dict[str, Any]]:
    return [d for (kind, name), d in docs.items() if kind == "Deployment" and "orchestrator" in name]


def _env(doc: dict[str, Any]) -> dict[str, str]:
    (container,) = doc["spec"]["template"]["spec"]["containers"]
    return {v["name"]: v.get("value", "") for v in container.get("env", [])}


def _builder(docs: Docs) -> dict[str, Any]:
    pod: dict[str, Any] = docs[("Deployment", "choregos-buildkitd")]["spec"]["template"]["spec"]
    return pod


def test_nothing_is_rendered_without_a_build_registry_and_the_default_render_is_unchanged() -> None:
    docs = _docs()
    assert not [key for key in docs if "buildkitd" in key[1]]
    assert all("CHOREGOS_BUILDKIT_ADDRESS" not in _env(w) for w in _workers(docs))


@pytest.mark.parametrize(
    "override",
    ["global.images.buildkitAddress=tcp://builder.ci:1234", "global.images.hostedBuilder=local_docker"],
)
def test_an_operators_own_builder_means_the_chart_deploys_none(override: str) -> None:
    docs = _docs(BUILDS, override)
    assert not [key for key in docs if "buildkitd" in key[1]]
    for worker in _workers(docs):
        address = _env(worker).get("CHOREGOS_BUILDKIT_ADDRESS")
        assert address == ("tcp://builder.ci:1234" if "buildkitAddress" in override else None)


def test_with_a_build_registry_the_workers_dial_the_charts_daemon() -> None:
    docs = _docs(BUILDS)
    assert ("Service", "choregos-buildkitd") in docs
    assert docs[("Service", "choregos-buildkitd")]["spec"]["ports"][0]["port"] == 1234
    for worker in _workers(docs):
        env = _env(worker)
        assert env["CHOREGOS_BUILDKIT_ADDRESS"] == "tcp://choregos-buildkitd:1234"
        assert "CHOREGOS_BUILDKIT_TLS_DIR" not in env


def test_the_builder_runs_rootless_with_nothing_but_what_rootless_buildkit_needs() -> None:
    pod = _builder(_docs(BUILDS))
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True and pod["securityContext"]["runAsUser"] == 1000
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    (container,) = pod["containers"]
    assert "@sha256:" in container["image"] and "-rootless" in container["image"]
    context = container["securityContext"]
    assert context["capabilities"] == {"drop": ["ALL"], "add": ["SETUID", "SETGID"]}
    assert context["readOnlyRootFilesystem"] is True and "privileged" not in context
    assert context["seccompProfile"] == context["appArmorProfile"] == {"type": "Unconfined"}
    assert "--oci-worker-no-process-sandbox" in container["args"]
    assert not container.get("env") and not container.get("envFrom"), "no platform credential"
    assert {v["name"] for v in pod["volumes"]} == {"state", "run", "tmp", "rootlesskit"}


def test_the_ci_daemon_writes_where_the_charts_does_and_rootlesskit_can_start() -> None:
    """The `hosted-build` job runs the chart's profile by hand: its read-only root leaves writable
    only what the chart mounts, and rootlesskit needs the image's TMPDIR before anything starts."""
    (container,) = _builder(_docs(BUILDS))["containers"]
    writable = {m["mountPath"] for m in container["volumeMounts"] if not m.get("readOnly")}
    workflow = (CHART.parents[1] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    step = workflow.split("docker run -d --name buildkitd", 1)[1].split("\n      - name:", 1)[0]
    tmpfs = {line.split()[1].split(":")[0] for line in step.splitlines() if "--tmpfs" in line}
    assert "/home/user/.local/tmp" in writable
    assert tmpfs == writable


def test_the_strict_rule_faults_the_builder_for_one_thing_and_an_exception_names_it_alone() -> None:
    plain = _docs(BUILDS)
    assert manquements(_builder(plain)) == ["buildkitd.allowPrivilegeEscalation"]
    assert ("PolicyException", "choregos-buildkitd") not in plain, "no Kyverno, no exception to render"
    docs = _docs(BUILDS, api_versions=(KYVERNO,))
    exception = docs[("PolicyException", "choregos-buildkitd")]["spec"]
    assert exception["exceptions"] == [
        {"policyName": "choregos-pod-security", "ruleNames": ["non-root", "autogen-non-root"]}
    ]
    (match,) = exception["match"]["any"]
    assert match["resources"]["names"] == ["choregos-buildkitd*"]
    assert match["resources"]["namespaces"] == ["choregos"]


def test_no_other_pod_of_the_chart_runs_unconfined() -> None:
    docs = _docs(BUILDS, "global.egress.enabled=true", "demoFakes.enabled=true")
    unconfined = []
    for (kind, name), doc in docs.items():
        if kind != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"]["containers"]:
            context = container.get("securityContext") or {}
            if "Unconfined" in str(context) or context.get("allowPrivilegeEscalation") is not False:
                unconfined.append(f"{name}/{container['name']}")
    assert unconfined == ["choregos-buildkitd/buildkitd"]


def test_only_the_orchestrator_reaches_it_and_it_reaches_the_internet_never_the_platform() -> None:
    docs = _docs(BUILDS)
    policy = docs[("NetworkPolicy", "choregos-buildkitd-net")]["spec"]
    assert policy["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": "choregos-buildkitd"}}
    (ingress,) = policy["ingress"]
    assert ingress["from"] == [
        {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "choregos-orchestrator"}}}
    ]
    assert ingress["ports"] == [{"protocol": "TCP", "port": 1234}]
    dns, internet = policy["egress"]
    assert dns["to"] == [
        {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}
    ]
    (block,) = internet["to"]
    assert block["ipBlock"]["cidr"] == "0.0.0.0/0" and set(block["ipBlock"]["except"]) == PRIVATE
    labels = docs[("Deployment", "choregos-buildkitd")]["spec"]["template"]["metadata"]["labels"]
    assert "app.kubernetes.io/part-of" not in labels, "`choregos-allow-ingress` would admit every pod"

    platform = docs[("NetworkPolicy", "choregos-allow-platform")]["spec"]
    (excluded,) = platform["podSelector"]["matchExpressions"]
    assert "choregos-buildkitd" in excluded["values"], "the builder never reaches PostgreSQL or Temporal"
    to_builder = [r for r in platform["egress"] if "choregos-buildkitd" in str(r["to"])]
    assert to_builder and to_builder[0]["ports"] == [{"protocol": "TCP", "port": 1234}]


def test_a_registry_in_a_private_range_is_opened_by_name_only() -> None:
    docs = _docs(BUILDS, "global.buildkitd.registryCidrs={10.20.0.5/32}")
    egress = docs[("NetworkPolicy", "choregos-buildkitd-net")]["spec"]["egress"]
    assert egress[-1] == {
        "to": [{"ipBlock": {"cidr": "10.20.0.5/32"}}],
        "ports": [{"protocol": "TCP", "port": 443}, {"protocol": "TCP", "port": 5000}],
    }


def test_mutual_tls_and_the_push_credential_come_from_secrets() -> None:
    docs = _docs(
        BUILDS,
        "global.buildkitd.tls.serverSecret=buildkitd-server",
        "global.buildkitd.tls.clientSecret=buildkitd-client",
        "global.buildkitd.pushSecret=build-push",
    )
    pod = _builder(docs)
    (container,) = pod["containers"]
    args = container["args"]
    assert args[args.index("--tlscacert") + 1] == "/certs/ca.crt"
    assert args[args.index("--tlscert") + 1] == "/certs/tls.crt"
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert volumes["certs"]["secret"] == {"secretName": "buildkitd-server"}
    assert volumes["push"]["secret"]["items"] == [{"key": ".dockerconfigjson", "path": "config.json"}]
    mounts = {m["name"]: m for m in container["volumeMounts"]}
    assert mounts["push"]["mountPath"] == "/home/user/.docker" and mounts["push"]["readOnly"] is True
    for worker in _workers(docs):
        assert _env(worker)["CHOREGOS_BUILDKIT_TLS_DIR"] == "/etc/choregos/buildkit"
        spec = worker["spec"]["template"]["spec"]
        assert {"name": "buildkit-client", "secret": {"secretName": "buildkitd-client"}} in spec["volumes"]
        assert "build-push" not in str(spec), "the push credential stays in the builder"
