"""`choregos-plugin-server-images` (ADR 0096, S43-06), read in Python.

`make policies-test` lets the Kyverno engine judge the pods of `infra/policies/tests/plugin-servers`;
this test plays the same expectations against the policy's own regular expression (RE2 and Python
agree on it), so a policy weakened to admit a tag or another registry turns the suite red where the
Kyverno CLI is not installed. It also proves the API's own manifest passes the policy.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import pytest
import yaml
from choregos_adapters.plugin_servers import ServerDeployment, server_manifests

ROOT = pathlib.Path(__file__).resolve().parents[2]
POLICY = ROOT / "infra" / "policies" / "plugin-servers.yaml"
CASES = ROOT / "infra" / "policies" / "tests" / "plugin-servers"


def _rule() -> dict[str, Any]:
    policy = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    (rule,) = policy["spec"]["rules"]
    return dict(rule)


def _regex() -> re.Pattern[str]:
    foreach = _rule()["validate"]["foreach"]
    assert [f["list"] for f in foreach] == [
        "request.object.spec.containers",
        "request.object.spec.initContainers || `[]`",
    ]
    keys = {f["deny"]["conditions"]["all"][0]["key"] for f in foreach}
    assert len(keys) == 1, "containers and init containers are judged alike"
    found = re.search(r"regex_match\('(.+)', '\{\{ element.image \}\}'\)", keys.pop())
    assert found is not None
    return re.compile(found.group(1))


def _admitted(pod: dict[str, Any]) -> bool:
    regex = _regex()
    images = [c["image"] for c in [*pod["spec"]["containers"], *pod["spec"].get("initContainers", [])]]
    return all(regex.fullmatch(image) for image in images)


def test_the_policy_judges_the_plugins_namespace_only() -> None:
    namespaces = _rule()["match"]["any"][0]["resources"]["namespaces"]
    assert namespaces == ["choregos-plugins"]
    assert "plugin-servers.yaml" in (ROOT / "infra" / "policies" / "kustomization.yaml").read_text()


def _cases() -> list[tuple[dict[str, Any], str]]:
    pods = {d["metadata"]["name"]: d for d in yaml.safe_load_all((CASES / "resources.yaml").read_text()) if d}
    test = yaml.safe_load((CASES / "kyverno-test.yaml").read_text())
    return [(pods[r["resources"][0]], r["result"]) for r in test["results"]]


@pytest.mark.parametrize(("pod", "result"), _cases(), ids=[p["metadata"]["name"] for p, _ in _cases()])
def test_the_engine_s_expectations(pod: dict[str, Any], result: str) -> None:
    assert _admitted(pod) == (result == "pass")


def test_the_api_s_own_server_passes() -> None:
    image = "ghcr.io/vargafoundation/choregos-agents/servicenow-mcp@sha256:" + "0" * 64
    d = ServerDeployment("s-1", "choregos-plugins", image, 8080, "/healthz", {"A": "b"}, {})
    deployment = next(o for o in server_manifests(d) if o["kind"] == "Deployment")
    assert _admitted({"spec": deployment["spec"]["template"]["spec"]})
