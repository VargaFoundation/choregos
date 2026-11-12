# SPDX-License-Identifier: Apache-2.0
"""ADR 0153: a human actor names a group, a person (`user`, an e-mail), or both — never nobody.

The schema and the model accept and refuse the same documents, and a group-only actor serialises as
it did: `user` absent is left out of the stored spec.
"""

from __future__ import annotations

import copy
from typing import Any

import choregos_contracts as contracts
import pytest
from choregos_contracts import HumanActor, Workflow

from .test_contracts import _compare, _load
from .test_decision_contract import _both


def _workflow_with(owner: dict[str, Any]) -> dict[str, Any]:
    workflow = copy.deepcopy(_load("workflow.default-simple.json"))
    workflow["actors"]["owner"] = owner
    return workflow


@pytest.mark.parametrize(
    ("owner", "accepted"),
    [
        ({"type": "human", "group": "product-owners"}, True),
        ({"type": "human", "user": "lea@varga.dev"}, True),
        ({"type": "human", "user": "lea@varga.dev", "group": "product-owners", "sla_hours": 4}, True),
        ({"type": "human", "sla_hours": 4}, False),
        ({"type": "human", "user": "not an e-mail"}, False),
    ],
)
def test_the_schema_and_the_model_agree_on_who_answers(owner: dict[str, Any], accepted: bool) -> None:
    assert _both(_workflow_with(owner)) == (accepted, accepted)


def test_a_human_actor_naming_nobody_says_what_it_needs() -> None:
    with pytest.raises(ValueError, match="`group`, a `user`"):
        HumanActor.model_validate({"type": "human"})


def test_the_human_actor_declares_the_same_fields_in_the_schema_and_the_model() -> None:
    gaps: list[str] = []
    _compare(
        "workflow.humanActor",
        contracts.load_schema("workflow.schema.json")["$defs"]["humanActor"],
        HumanActor,
        gaps,
    )
    assert not gaps, "\n".join(gaps)
    assert "user" in HumanActor.model_fields


def test_a_group_only_actor_is_stored_as_it_was() -> None:
    document = _load("workflow.default-simple.json")
    dumped = Workflow.model_validate(document).model_dump(mode="json", by_alias=True, exclude_none=True)
    assert dumped["actors"]["owner"] == {"type": "human", "group": "product-owners", "sla_hours": 24}
