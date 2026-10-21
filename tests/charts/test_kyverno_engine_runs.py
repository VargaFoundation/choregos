"""The Kyverno engine, not a Python reading, judges the policies in CI.

`infra/policies/tests` holds pods and the verdict each rule must give; `kyverno test` plays them.
Python cannot stand in for it: the image rule is an RE2 regular expression and the pod rules use
Kyverno anchors. These checks keep that wiring from coming undone quietly.
"""

from __future__ import annotations

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
POLICIES = ROOT / "infra" / "policies"


def test_every_policy_file_is_deployed() -> None:
    listed = set(yaml.safe_load((POLICIES / "kustomization.yaml").read_text())["resources"])
    files = {p.name for p in POLICIES.glob("*.yaml")} - {"kustomization.yaml"}
    assert files == listed, f"on disk but not deployed: {files - listed}; listed but absent: {listed - files}"


def test_the_charts_job_runs_kyverno_test() -> None:
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps = " ".join(str(s.get("run", "")) for s in ci["jobs"]["charts"]["steps"])
    assert "kyverno test infra/policies/tests" in steps
    assert "sha256sum -c" in steps, "the downloaded CLI must be checked"


def test_the_engine_judges_the_rules_that_need_it() -> None:
    test = yaml.safe_load((POLICIES / "tests" / "kyverno-test.yaml").read_text())
    judged = {(r["policy"], r["rule"], r["result"]) for r in test["results"]}
    for policy, rule in (("choregos-image-tags", "interdire-latest"), ("choregos-pod-security", "non-root")):
        assert {(policy, rule, "pass"), (policy, rule, "fail")} <= judged, f"{policy}/{rule}"
