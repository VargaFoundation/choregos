"""A human task (S20-06): what the validator requires (a person, an object form whose
every property is a declared field) and the sentence the process view makes of it."""

from __future__ import annotations

import pytest
from choregos_core.dsl import parse_workflow, to_process

BADGE = """
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: onboarding
  version: 1
  inputs:
    type: object
    properties:
      upn: {type: string}
      badge_uid: {type: string}
initial: badge
actors:
  reception: {type: human, group: reception}
states:
  badge: {display: Badge to hand over, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - id: t-badge
    from: badge
    to: done
    by: reception
    task:
      title: Hand over the badge
      instructions: In person, then pass it over the reader.
      form:
        type: object
        required: [badge_uid]
        properties:
          badge_uid: {type: string, minLength: 8}
      attest: I handed the badge to its holder in person
"""


def _codes(text: str) -> list[str]:
    _, report = parse_workflow(text, strict=False)
    return [e.code for e in report.errors]


def test_a_well_formed_task_is_valid_and_said_in_plain_words() -> None:
    workflow, report = parse_workflow(BADGE, strict=False)
    assert report.valid, report.as_dict()
    step = to_process(workflow)[0]
    assert step["task"] == (
        "done “Hand over the badge” (filling `badge_uid`) and attested “I handed the badge to its holder in "
        "person”"
    )
    assert "once they have done “Hand over the badge”" in step["sentence"]


FIELD = "          badge_uid: {type: string, minLength: 8}"
OBJECT = "        type: object\n        required: [badge_uid]"
PROPERTIES = "        properties:\n" + FIELD


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        ("  reception: {type: human, group: reception}", "  reception: {type: system}", "task.not_human"),
        (FIELD, "          number: {type: string}", "task.field_unknown"),
        (OBJECT, OBJECT.replace("object", "obj"), "task.form_invalid"),
        (PROPERTIES, "        properties: {}", "task.form_invalid"),
    ],
)
def test_the_validator_refuses_a_task_that_cannot_be_done(old: str, new: str, code: str) -> None:
    assert old in BADGE
    assert code in _codes(BADGE.replace(old, new))
