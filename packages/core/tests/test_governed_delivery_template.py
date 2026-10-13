"""The `software-delivery-governed` template (S33-07): its workflow validates, and it keeps the
contracts its catalogue agents state (S33-06) — and so does every other shipped template."""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_core.catalogue_d_agents import entree
from choregos_core.dsl.parser import parse_workflow

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
GOVERNED = TEMPLATES / "software-delivery-governed"
WORKFLOW = GOVERNED / "workflows" / "governed-delivery.yaml"
SHIPPED_WORKFLOWS = sorted(TEMPLATES.glob("*/workflows/*.yaml"))


def _doc(path: Path) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_the_workflow_validates_strictly() -> None:
    workflow, report = parse_workflow(WORKFLOW.read_text(encoding="utf-8"), strict=True)
    assert report.valid, [e.format() for e in report.errors]
    assert not report.warnings, [w.format() for w in report.warnings]
    assert workflow.metadata.name == "governed-delivery"


def test_the_happy_path_follows_the_plan_order() -> None:
    """study → architecture → plan → implement → QA, security, supply chain, compliance → docs →
    readiness → pull request → train → verification (plan 2026-10-12, item 3)."""
    by_id = {t["id"]: t for t in _doc(WORKFLOW)["transitions"]}
    path = [
        "t-triage", "t-study", "t-architecture", "t-approve-architecture", "t-specify", "t-plan",
        "t-implement", "t-qa", "t-security-review", "t-supply-chain", "t-compliance",
        "t-decide-compliance", "t-document", "t-release-readiness", "t-open-pr", "t-approve-pr",
        "t-merge", "t-stage", "t-release", "t-verify",
    ]  # fmt: skip
    for before, after in itertools.pairwise(path):
        assert by_id[before]["to"] == by_id[after]["from"], (before, after)
    assert _doc(WORKFLOW)["states"][by_id["t-verify"]["to"]].get("terminal")


def test_production_goes_through_the_train_under_the_captain() -> None:
    transitions = _doc(WORKFLOW)["transitions"]
    (to_live,) = [t for t in transitions if t["to"] == "live"]
    assert to_live["via"] == "release_train" and to_live["train"] == {"env": "prod", "approval": "captain"}


def test_the_template_installs_every_catalogue_agent_its_workflow_names() -> None:
    manifest = _doc(GOVERNED / "manifest.yaml")
    listed = {a.removeprefix("catalogue:") for a in manifest["defaults"]["agents"]}
    named = {a["agent"] for a in _doc(WORKFLOW)["actors"].values() if a.get("agent")}
    assert named == listed
    for slug in named:
        assert entree(slug) is not None, f"`{slug}` is not in the catalogue"
    for skill in manifest["defaults"]["skills"]:
        assert skill.startswith("catalogue:")


@pytest.mark.parametrize("path", SHIPPED_WORKFLOWS, ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_a_transition_declares_the_outputs_its_catalogue_agent_produces(path: Path) -> None:
    doc = _doc(path)
    actors = doc["actors"]
    for transition in doc["transitions"]:
        agent = entree(str(actors.get(transition.get("by"), {}).get("agent", "")))
        if agent is None or not agent.outputs:
            continue
        # A subset: an agent with two moments (architect: draft, then accept; devops: readiness, then
        # verification) produces part of its contract at each.
        outputs = set(transition.get("outputs") or [])
        assert outputs <= set(agent.outputs), (
            f"{transition['id']}: {outputs - set(agent.outputs)} not produced"
        )


@pytest.mark.parametrize("path", SHIPPED_WORKFLOWS, ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_an_agent_that_only_recommends_is_followed_by_people(path: Path) -> None:
    """`human_gate_after` (compliance): the state it leads to waits, and only people leave it."""
    doc = _doc(path)
    actors, states = doc["actors"], doc["states"]
    for transition in doc["transitions"]:
        agent = entree(str(actors.get(transition.get("by"), {}).get("agent", "")))
        if agent is None or not agent.human_gate_after:
            continue
        target = transition["to"]
        assert states[target].get("kind") == "wait", f"{transition['id']}: `{target}` does not wait"
        leaving = [t for t in doc["transitions"] if t["from"] == target]
        assert leaving and all(actors[t["by"]]["type"] == "human" for t in leaving), transition["id"]


def test_the_governed_workflow_does_run_compliance() -> None:
    """Without this, the check above could pass on a template that dropped the compliance stage."""
    actors = _doc(WORKFLOW)["actors"]
    assert any(a.get("agent") == "compliance" for a in actors.values())
