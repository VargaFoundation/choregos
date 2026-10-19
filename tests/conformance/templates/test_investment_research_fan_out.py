"""The investment-research campaign fans out one child per relevant product (S38-15, ADR 0057).

Read from the workflow files, not copied: the spawn of `campaign.yaml` names a workflow the
template delivers, a list the backtest writes, and gives the child every field its schema requires;
`product-memo.yaml` keeps the guarantees of the memo (numbers measured) and of the decision
(attested human task). Remove the `spawn:` and the first test goes red.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "investment-research"


def _workflow(name: str) -> Any:
    from choregos_core.dsl import parse_workflow

    workflow, report = parse_workflow((TEMPLATE / "workflows" / f"{name}.yaml").read_text(encoding="utf-8"))
    assert workflow is not None, report
    return workflow


def _transition(workflow: Any, transition_id: str) -> Any:
    (transition,) = [t for t in workflow.transitions if t.id == transition_id]
    return transition


def test_the_campaign_opens_one_child_per_relevant_product_after_the_verdict() -> None:
    campaign = _workflow("campaign")
    verdict, fan_out = _transition(campaign, "t-verdict"), _transition(campaign, "t-fan-out")
    assert fan_out.from_ == verdict.to, "the fan-out follows the verdict"
    assert fan_out.spawn is not None
    assert fan_out.spawn.workflow == "product-memo"
    assert fan_out.spawn.for_each == "outputs.metrics.relevant", "the list the backtest measured"
    assert fan_out.spawn.succeeded == ["adopted"] and fan_out.spawn.min_succeeded == 1
    assert fan_out.on_fail is not None and fan_out.on_fail.escalate_to == "not_adopted"
    # The campaign's own decision comes after the join, and is still an attested task.
    decide = _transition(campaign, "t-decide")
    assert decide.from_ == fan_out.to and decide.task is not None and decide.task.attest


def test_the_child_workflow_is_delivered_and_gets_every_field_it_requires() -> None:
    manifest = yaml.safe_load((TEMPLATE / "manifest.yaml").read_text(encoding="utf-8"))
    assert "./workflows/product-memo.yaml" in manifest["defaults"]["workflows"]
    campaign, child = _workflow("campaign"), _workflow("product-memo")
    spawn = _transition(campaign, "t-fan-out").spawn
    schema = child.metadata.inputs
    assert not set(schema["required"]) - set(spawn.fields), "a child the schema would refuse"
    assert not set(spawn.fields) - set(schema["properties"]), "a field the child does not declare"


def test_the_memo_and_the_decision_keep_their_guarantees_in_the_child() -> None:
    child, campaign = _workflow("product-memo"), _workflow("campaign")
    memo = _transition(child, "t-memo")
    assert [(g.name, dict(g.params or {})) for g in memo.gates] == [
        (
            "numbers_measured",
            {
                "sources": ["parent.metrics.products.$symbol", "parent.metrics.facts"],
                "outputs": ["memo_markdown"],
            },
        )
    ]
    # Never the child's own fields: a person can edit them (S38-17).
    assert "fields" not in memo.gates[0].params["sources"]
    decide = _transition(child, "t-decide")
    assert decide.task is not None and decide.task.attest and decide.on_reject == "rejected"
    assert child.states["adopted"].terminal and child.states["rejected"].terminal
    # The campaign no longer writes a memo of its own: no agent step is left in it.
    assert [n for n, a in campaign.actors.items() if getattr(a, "agent", None)] == ["strategist"]
