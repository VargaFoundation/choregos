# SPDX-License-Identifier: Apache-2.0
"""`pull_request: {draft: true}` (S51-11): only a system transition that opens the pull request says
how it is opened, and only with the keys the contract knows."""

from __future__ import annotations

import pytest
from choregos_core import parse_workflow
from choregos_core.dsl import template_yaml
from choregos_core.dsl.edition import edit
from choregos_core.errors import ValidationError

OPEN_PR = "    does: open_pr\n"


def _with(text: str, line: str = "    pull_request: { draft: true }\n") -> str:
    assert text.count(OPEN_PR) == 1
    return text.replace(OPEN_PR, OPEN_PR + line)


def test_a_transition_that_opens_the_pull_request_may_open_it_as_a_draft() -> None:
    wf, report = parse_workflow(_with(template_yaml("default-simple")), strict=False)
    assert report.valid, [e.message for e in report.errors]
    (opening,) = [t for t in wf.transitions if t.id == "t-open-pr"]
    assert opening.pull_request is not None and opening.pull_request.draft is True


def test_without_it_nothing_changes() -> None:
    wf, report = parse_workflow(template_yaml("default-simple"), strict=False)
    assert report.valid
    assert all(t.pull_request is None for t in wf.transitions)


def test_the_typed_edition_writes_it() -> None:
    edited = edit(
        template_yaml("default-simple"),
        [{"op": "set_transition", "id": "t-open-pr", "field": "pull_request", "value": {"draft": True}}],
    )
    wf, report = parse_workflow(edited.yaml, strict=False)
    assert report.valid, [e.message for e in report.errors]
    assert [t.id for t in wf.transitions if t.pull_request and t.pull_request.draft] == ["t-open-pr"]


@pytest.mark.parametrize("transition", ["t-merge", "t-approve-spec"])
def test_a_transition_that_does_not_open_the_pull_request_cannot_say_how(transition: str) -> None:
    edited = edit(
        template_yaml("default-simple"),
        [{"op": "set_transition", "id": transition, "field": "pull_request", "value": {"draft": True}}],
    )
    _, report = parse_workflow(edited.yaml, strict=False)
    assert [e.code for e in report.errors] == ["transition.pull_request_without_open_pr"], report.errors


def test_an_unknown_key_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"pull_request\.drafty"):
        parse_workflow(_with(template_yaml("default-simple"), "    pull_request: { drafty: true }\n"))
