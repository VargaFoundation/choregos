"""Issue codes renamed to English accept both spellings for one release (ADR 0046, S34-03)."""

from __future__ import annotations

import choregos_contracts as contracts
import pytest
from choregos_contracts.issue_codes import (
    RENAMED_ISSUE_CODES,
    canonical_issue_code,
    deprecated_issue_code,
)

PAIRS = [
    ("gate.sans_matiere", "gate.nothing_to_check"),  # lang: fr-ok
    ("workflow.effet_implicite", "workflow.implicit_effect"),  # lang: fr-ok
]


@pytest.mark.parametrize(("former", "english"), PAIRS)
def test_both_spellings_resolve_to_the_english_code(former: str, english: str) -> None:
    assert canonical_issue_code(former) == english
    assert canonical_issue_code(english) == english


@pytest.mark.parametrize(("former", "english"), PAIRS)
def test_the_former_spelling_is_known_from_either_spelling(former: str, english: str) -> None:
    assert deprecated_issue_code(english) == former
    assert deprecated_issue_code(former) == former


def test_a_code_never_renamed_is_left_alone() -> None:
    assert canonical_issue_code("state.unknown") == "state.unknown"
    assert deprecated_issue_code("state.unknown") is None


def test_the_renamed_codes_are_exactly_the_two_of_adr_0046() -> None:
    assert dict(PAIRS) == RENAMED_ISSUE_CODES


def test_the_contract_declares_the_former_code_as_deprecated() -> None:
    schemas = contracts.load_openapi()["components"]["schemas"]
    issue = schemas["WorkflowIssue"]["properties"]["deprecated_code"]
    problem_error = schemas["Problem"]["properties"]["errors"]["items"]["properties"]
    assert issue["deprecated"] is True
    assert problem_error["deprecated_code"]["deprecated"] is True
    assert "code" in problem_error
