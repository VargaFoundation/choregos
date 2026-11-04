"""Our pods satisfy the strict Kyverno rule -- checked on the rendered chart.

`infra/policies/pod-security.yaml` requires, for every pod in `choregos`, a `choregos-*` or a
`proj-*` namespace: `runAsNonRoot`, a seccomp profile at pod level, and for each container
`allowPrivilegeEscalation: false` and `capabilities.drop: [ALL]`. A strict rule that
would refuse our own pods would be a chart that does not install: this test renders the chart
for each environment and applies the rule to each pod template. Without `helm`, it
skips and says so.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"
ENVIRONMENTS = ("local", "dev", "staging", "prod")

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _rendered(env: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["helm", "template", "choregos", str(CHART), "-f", str(CHART / "values" / f"{env}.yaml")],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def _pod_templates(documents: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    output: list[tuple[str, dict[str, Any]]] = []
    for doc in documents:
        kind, name = doc.get("kind"), doc.get("metadata", {}).get("name", "?")
        if kind in {"Deployment", "StatefulSet", "Job", "Rollout"}:
            output.append((f"{kind}/{name}", doc["spec"]["template"]["spec"]))
        elif kind == "CronJob":
            output.append((f"{kind}/{name}", doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]))
        elif kind == "Pod":
            output.append((f"{kind}/{name}", doc["spec"]))
    return output


def _confined(profile: dict[str, Any] | None) -> bool:
    """An absent profile passes (`=()` anchor); a present one must be RuntimeDefault or Localhost."""
    return profile is None or profile.get("type") in {"RuntimeDefault", "Localhost"}


def violations(spec: dict[str, Any]) -> list[str]:
    """What the `non-root` rule would fault this pod for; empty if it passes."""
    fautes: list[str] = []
    pod = spec.get("securityContext") or {}
    if pod.get("runAsNonRoot") is not True:
        fautes.append("pod.runAsNonRoot")
    if (pod.get("seccompProfile") or {}).get("type") not in {"RuntimeDefault", "Localhost"}:
        fautes.append("pod.seccompProfile")
    if not _confined(pod.get("appArmorProfile")):
        fautes.append("pod.appArmorProfile")
    # Ephemeral containers: only their profiles are judged (as in the policy).
    for conteneur in spec.get("ephemeralContainers", []):
        context = conteneur.get("securityContext") or {}
        for profile in ("seccompProfile", "appArmorProfile"):
            if not _confined(context.get(profile)):
                fautes.append(f"{conteneur['name']}.{profile}")
    for conteneur in spec.get("containers", []) + spec.get("initContainers", []):
        context = conteneur.get("securityContext") or {}
        # A container's own profile overrides the pod's (finding #784).
        for profile in ("seccompProfile", "appArmorProfile"):
            if not _confined(context.get(profile)):
                fautes.append(f"{conteneur['name']}.{profile}")
        if context.get("allowPrivilegeEscalation") is not False:
            fautes.append(f"{conteneur['name']}.allowPrivilegeEscalation")
        if "ALL" not in ((context.get("capabilities") or {}).get("drop") or []):
            fautes.append(f"{conteneur['name']}.capabilities.drop")
    return fautes


@pytest.mark.parametrize("env", ENVIRONMENTS)
def test_every_pod_of_the_chart_passes_the_non_root_rule(env: str) -> None:
    offenders = {name: violations(spec) for name, spec in _pod_templates(_rendered(env))}
    assert {name: f for name, f in offenders.items() if f} == {}, (
        f"{env}: the Kyverno rule would refuse these pods"
    )


def test_the_kyverno_rule_is_strict() -> None:
    """The file itself: no more conditional anchor on the fields that matter."""
    policy = yaml.safe_load((ROOT / "infra" / "policies" / "pod-security.yaml").read_text())
    rule = next(r for r in policy["spec"]["rules"] if r["name"] == "non-root")
    motif = rule["validate"]["pattern"]["spec"]
    assert motif["securityContext"]["runAsNonRoot"] is True, "`=(securityContext)` laissait passer un pod nu"
    assert "seccompProfile" in motif["securityContext"]
    assert motif["containers"][0]["securityContext"]["allowPrivilegeEscalation"] is False


def _pod(**surcharges: Any) -> dict[str, Any]:
    conteneur = {
        "name": "main",
        "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
    }
    spec: dict[str, Any] = {
        "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
        "containers": [conteneur],
    }
    spec.update(surcharges)
    return spec


def test_a_profile_set_on_a_container_or_an_init_container_is_judged() -> None:
    """Finding #784: the pod's RuntimeDefault used to hide a container's own `Unconfined`."""
    assert violations(_pod()) == []
    unconfined = {"type": "Unconfined"}
    for profile in ("seccompProfile", "appArmorProfile"):
        spec = _pod()
        spec["containers"][0]["securityContext"][profile] = unconfined
        assert violations(spec) == [f"main.{profile}"]
        init = {**_pod()["containers"][0], "name": "side"}
        init["securityContext"] = {**init["securityContext"], profile: unconfined}
        assert violations(_pod(initContainers=[init])) == [f"side.{profile}"]
        assert violations(
            _pod(ephemeralContainers=[{"name": "dbg", "securityContext": {profile: unconfined}}])
        ) == [f"dbg.{profile}"]
    spec = _pod()
    spec["securityContext"]["appArmorProfile"] = unconfined
    assert violations(spec) == ["pod.appArmorProfile"]
    spec = _pod()
    spec["containers"][0]["securityContext"]["seccompProfile"] = {
        "type": "Localhost",
        "localhostProfile": "x",
    }
    assert violations(spec) == []


def test_the_policy_file_judges_container_profiles() -> None:
    policy = yaml.safe_load((ROOT / "infra" / "policies" / "pod-security.yaml").read_text())
    rule = next(r for r in policy["spec"]["rules"] if r["name"] == "non-root")
    motif = rule["validate"]["pattern"]["spec"]
    assert "=(appArmorProfile)" in motif["securityContext"]
    for key in ("containers", "=(initContainers)"):
        context = motif[key][0]["securityContext"]
        assert "=(seccompProfile)" in context and "=(appArmorProfile)" in context, key
