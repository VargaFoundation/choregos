"""The Kyverno latest-tag and privilege rules also judge `spec.initContainers` (finding #770).

Native sidecars are init containers with `restartPolicy: Always`: a rule that reads only
`spec.containers` lets a privileged or `:latest` sidecar through. The anchor must be optional
(`=(initContainers)`) so a pod without any init container still passes.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
POLICIES = ROOT / "infra" / "policies"


def _pattern(file: str, rule: str) -> dict[str, Any]:
    policy = yaml.safe_load((POLICIES / file).read_text())
    found = next(r for r in policy["spec"]["rules"] if r["name"] == rule)
    spec: dict[str, Any] = found["validate"]["pattern"]["spec"]
    return spec


def _latest_rule() -> dict[str, Any]:
    policy = yaml.safe_load((POLICIES / "image-tags.yaml").read_text())
    rule: dict[str, Any] = next(r for r in policy["spec"]["rules"] if r["name"] == "interdire-latest")
    return rule


def _refusal_regex() -> re.Pattern[str]:
    """The regex the `deny` conditions hand to `regex_match` (Go RE2; this one is also valid
    Python), read from the policy so the test judges the shipped expression, not a copy."""
    foreach = _latest_rule()["validate"]["foreach"]
    assert [f["list"] for f in foreach] == [
        "request.object.spec.containers",
        "request.object.spec.initContainers || `[]`",
    ], "containers AND init containers (native sidecars), the latter optional"
    found: set[str] = set()
    for entry in foreach:
        condition = entry["deny"]["conditions"]["all"][0]
        assert condition["operator"] == "Equals" and condition["value"] is True
        match = re.fullmatch(r"\{\{ regex_match\('(.*)', '\{\{ element\.image \}\}'\) \}\}", condition["key"])
        assert match, condition["key"]
        found.add(match.group(1))
    assert len(found) == 1, "both lists must use the same expression"
    return re.compile(found.pop())


def _refused(image: str) -> bool:
    return _refusal_regex().search(image) is not None


@pytest.mark.parametrize(
    "image",
    [
        "nginx",
        "nginx:latest",
        "ghcr.io/org/img",
        "ghcr.io/org/img:latest",
        "registry:5000/img",
        "registry:5000/img:latest",
    ],
)
def test_an_image_resolving_to_latest_is_refused(image: str) -> None:
    assert _refused(image), image


@pytest.mark.parametrize(
    "image",
    [
        "nginx:1.27",
        "ghcr.io/a/b:1.0",
        "ghcr.io/a/b@sha256:abc",
        "ghcr.io/a/b:1.0@sha256:abc",
        "ghcr.io/a/b:latest@sha256:abc",
        "registry:5000/img:2",
        "registry:5000/img@sha256:abc",
    ],
)
def test_a_tagged_or_digest_pinned_image_passes(image: str) -> None:
    assert not _refused(image), image


def test_the_latest_tag_rule_names_ephemeral_containers_as_out_of_scope() -> None:
    text = (POLICIES / "image-tags.yaml").read_text()
    assert "Ephemeral containers" in text and "OUT of this rule on purpose" in text
    for rule in yaml.safe_load(text)["spec"]["rules"]:
        assert "ephemeralContainers" not in str(rule)


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")
@pytest.mark.parametrize("env", ["local", "dev", "staging", "prod"])
def test_every_image_the_chart_renders_has_a_tag_or_a_digest(env: str) -> None:
    chart = ROOT / "charts" / "choregos"
    rendered = subprocess.run(
        ["helm", "template", "choregos", str(chart), "-f", str(chart / "values" / f"{env}.yaml")],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout
    images: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"containers", "initContainers"} and isinstance(value, list):
                    images.extend(c["image"] for c in value if "image" in c)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for document in yaml.safe_load_all(rendered):
        walk(document)
    assert images, "no image found: the walk no longer reads the render"
    assert [i for i in images if _refused(i)] == []


def test_the_privilege_rules_cover_init_containers() -> None:
    spec = _pattern("pod-security.yaml", "non-root")
    assert "initContainers" not in spec, "a mandatory anchor would refuse pods without any"
    assert spec["=(initContainers)"] == spec["containers"]
    context = spec["=(initContainers)"][0]["securityContext"]
    assert context["allowPrivilegeEscalation"] is False
    assert context["=(privileged)"] is False
    assert context["capabilities"]["drop"] == ["ALL"]
