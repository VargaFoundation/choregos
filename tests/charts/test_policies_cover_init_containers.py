"""The Kyverno latest-tag and privilege rules also judge `spec.initContainers` (finding #770).

Native sidecars are init containers with `restartPolicy: Always`: a rule that reads only
`spec.containers` lets a privileged or `:latest` sidecar through. The anchor must be optional
(`=(initContainers)`) so a pod without any init container still passes.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
POLICIES = ROOT / "infra" / "policies"


def _pattern(file: str, rule: str) -> dict[str, Any]:
    policy = yaml.safe_load((POLICIES / file).read_text())
    found = next(r for r in policy["spec"]["rules"] if r["name"] == rule)
    spec: dict[str, Any] = found["validate"]["pattern"]["spec"]
    return spec


def test_the_latest_tag_rule_covers_init_containers() -> None:
    spec = _pattern("images.yaml", "interdire-latest")
    assert spec["containers"] == [{"image": "!*:latest"}]
    assert spec["=(initContainers)"] == [{"image": "!*:latest"}]


def test_the_privilege_rules_cover_init_containers() -> None:
    spec = _pattern("pod-security.yaml", "non-root")
    assert "initContainers" not in spec, "a mandatory anchor would refuse pods without any"
    assert spec["=(initContainers)"] == spec["containers"]
    context = spec["=(initContainers)"][0]["securityContext"]
    assert context["allowPrivilegeEscalation"] is False
    assert context["=(privileged)"] is False
    assert context["capabilities"]["drop"] == ["ALL"]
