"""The `github-software-delivery` template: `dev-complex` documents its change (S39-15)."""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import yaml
from choregos_core.catalogue_d_agents import entree
from choregos_core.dsl.parser import parse_workflow

TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "github-software-delivery"
WORKFLOWS = TEMPLATE / "workflows"
COMPLEX = WORKFLOWS / "dev-complex.yaml"


def _doc(path: Path) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_dev_complex_validates_strictly_in_version_2() -> None:
    workflow, report = parse_workflow(COMPLEX.read_text(encoding="utf-8"), strict=True)
    assert report.valid, [e.format() for e in report.errors]
    assert not report.warnings, [w.format() for w in report.warnings]
    assert workflow.metadata.version == 2


def test_the_happy_path_goes_through_the_documentation_step() -> None:
    by_id = {t["id"]: t for t in _doc(COMPLEX)["transitions"]}
    path = [
        "t-triage", "t-specify", "t-approve-spec", "t-plan", "t-implement", "t-test", "t-document",
        "t-review", "t-security-review", "t-release-notes", "t-open-pr", "t-approve-pr", "t-merge",
        "t-stage", "t-release", "t-verify-prod",
    ]  # fmt: skip
    for before, after in itertools.pairwise(path):
        assert by_id[before]["to"] == by_id[after]["from"], (before, after)


def test_the_documentation_step_is_carried_by_the_docs_writer_under_its_gates() -> None:
    doc = _doc(COMPLEX)
    step = {t["id"]: t for t in doc["transitions"]}["t-document"]
    actor = doc["actors"][step["by"]]
    assert actor["agent"] == "docs-writer" and actor["role"] == "document"
    assert step["outputs"] == ["docs_report"]
    assert {"scope_respected", "no_secrets", "docs_updated"} <= set(step["gates"])


def test_the_release_notes_read_the_documentation_report() -> None:
    step = {t["id"]: t for t in _doc(COMPLEX)["transitions"]}["t-release-notes"]
    assert "docs_report" in step["inputs"]


def test_the_manifest_installs_exactly_the_agents_the_workflows_name() -> None:
    manifest = _doc(TEMPLATE / "manifest.yaml")
    assert manifest["metadata"]["version"] == "1.1.0"
    listed = {a.removeprefix("catalogue:") for a in manifest["defaults"]["agents"]}
    named = {
        a["agent"]
        for path in sorted(WORKFLOWS.glob("*.yaml"))
        for a in _doc(path)["actors"].values()
        if a.get("agent")
    }
    # `researcher` and `architect` are named by `study`, which is one of the three workflows.
    assert listed == named
    for slug in named:
        assert entree(slug) is not None, f"`{slug}` is not in the catalogue"
