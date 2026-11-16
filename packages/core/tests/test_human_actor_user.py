# SPDX-License-Identifier: Apache-2.0
"""ADR 0153: a human step addressed to a person — the digest of a group-only workflow does not move,
the process view says who answers, and a train's approver still names a group."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from choregos_contracts import Workflow
from choregos_core.dsl.parser import checksum, parse_workflow
from choregos_core.dsl.process import to_process

EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "schemas"
    / "examples"
    / "workflow.default-simple.json"
)

#: The digest of the group-only example as the platform pinned it before ADR 0153: work items pinned to
#: it must still find it.
GROUP_ONLY_DIGEST = "sha256:76353f17e89f4debc2ddc8db834f521faacbbb892a0201ef81a1c7f580365e2e"


def _example() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    return document


def test_the_digest_of_a_group_only_workflow_does_not_move() -> None:
    assert checksum(Workflow.model_validate(_example())) == GROUP_ONLY_DIGEST


def test_the_process_view_says_the_person_is_asked_first() -> None:
    document = _example()
    document["actors"]["owner"] = {"type": "human", "user": "lea@varga.dev", "group": "product-owners"}
    workflow = Workflow.model_validate(document)
    steps = [s for s in to_process(workflow) if s["actor"] == "owner"]
    assert steps and steps[0]["user"] == "lea@varga.dev"
    assert "`lea@varga.dev`, then a person of group `product-owners`" in steps[0]["who"]

    document["actors"]["owner"] = {"type": "human", "user": "lea@varga.dev"}
    alone = [s for s in to_process(Workflow.model_validate(document)) if s["actor"] == "owner"]
    assert alone[0]["who"] == "`lea@varga.dev`" and alone[0]["group"] is None


TRAIN = """
apiVersion: choregos/v1
kind: Workflow
metadata: {name: ship, version: 1}
initial: ready
actors:
  captain: {type: human, user: lea@varga.dev}
states:
  ready: {display: Ready, kind: wait}
  shipped: {display: Shipped, terminal: true}
transitions:
  - id: t-ship
    from: ready
    to: shipped
    via: release_train
    train: {env: prod, approval: captain}
"""


def test_a_train_approver_names_a_group() -> None:
    _, report = parse_workflow(TRAIN, strict=False)
    codes = [issue.code for issue in report.errors]
    assert "actor.train_approver_group" in codes, codes
    _, grouped = parse_workflow(TRAIN.replace("user: lea@varga.dev", "group: captains"), strict=False)
    assert "actor.train_approver_group" not in [issue.code for issue in grouped.errors]
