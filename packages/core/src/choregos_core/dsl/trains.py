# SPDX-License-Identifier: Apache-2.0
"""What a workflow asks of the train it takes (ADR 0041).

The product review of 2026-10-07 found it: `train: {env: prod, approval: captain}` was written
in the shipped workflows and read by nobody. The orchestrator passed the train only the
environment, and the train drew its approval ONLY from the project's policy, so a project
could not ship a fix without a human and a feature with one. These functions say, from the
work item's PINNED workflow, what its train requires.
"""

from __future__ import annotations

from typing import TypedDict

from choregos_contracts import HumanActor, SystemActor, Workflow
from choregos_contracts.workflow import Transition, transition_effect


class TrainApproval(TypedDict):
    """What a work item requires of its batch's departure: a dict, because it travels in Temporal signals."""

    required: bool
    group: str | None


def takes_the_train(transition: Transition) -> bool:
    return transition.via == "release_train" or transition.train is not None


def train_env(transition: Transition) -> str:
    """The target environment; `prod` when the transition does not say (`via: release_train` alone)."""
    return transition.train.env if transition.train else "prod"


def train_approval(workflow: Workflow, transition: Transition) -> TrainApproval:
    """`train.approval` names a human actor: their group approves the departure. An actor that is
    not human still requires the approval; the group will then be the policy's."""
    if transition.train is None or not transition.train.approval:
        return {"required": False, "group": None}
    actor = workflow.actors.get(str(transition.train.approval))
    return {"required": True, "group": actor.group if isinstance(actor, HumanActor) else None}


def train_after_merge(workflow: Workflow) -> Transition | None:
    """The train a work item takes right after its PR merges, or `None` when it takes none:
    a study that merges an ADR goes nowhere, a prudent delivery goes through staging first.
    The GitHub webhook used to ship every merged work item to PROD, whatever its workflow."""
    for merge in workflow.transitions:
        actor = workflow.actors.get(merge.by or "")
        if not isinstance(actor, SystemActor) or transition_effect(merge) != "merge_pr":
            continue
        for follow_up in workflow.transitions:
            if follow_up.from_ == merge.to and takes_the_train(follow_up):
                return follow_up
    return None


__all__ = [
    "TrainApproval",
    "takes_the_train",
    "train_after_merge",
    "train_approval",
    "train_env",
]
