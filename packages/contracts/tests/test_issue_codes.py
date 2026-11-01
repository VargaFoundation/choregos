"""Issue codes renamed to English accept both spellings for one release (ADR 0046, S34-03, S45-02)."""

from __future__ import annotations

import choregos_contracts as contracts
import pytest
from choregos_contracts.issue_codes import (
    RENAMED_ISSUE_CODES,
    canonical_issue_code,
    deprecated_issue_code,
)

PAIRS = [
    ("role.playbook_introuvable", "role.playbook_not_found"),  # lang: fr-ok
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


def test_role_playbook_code_is_english_with_its_former_spelling() -> None:
    assert canonical_issue_code("role.playbook_introuvable") == "role.playbook_not_found"  # lang: fr-ok
    # The S34-03 window ended: these two are no longer aliases.
    assert canonical_issue_code("gate.sans_matiere") == "gate.sans_matiere"  # lang: fr-ok
    assert canonical_issue_code("workflow.effet_implicite") == "workflow.effet_implicite"  # lang: fr-ok
    assert deprecated_issue_code("gate.nothing_to_check") is None


def test_the_renamed_codes_are_exactly_the_one_left() -> None:
    assert dict(PAIRS) == RENAMED_ISSUE_CODES


def test_the_contract_declares_the_former_code_as_deprecated() -> None:
    schemas = contracts.load_openapi()["components"]["schemas"]
    issue = schemas["WorkflowIssue"]["properties"]["deprecated_code"]
    problem_error = schemas["Problem"]["properties"]["errors"]["items"]["properties"]
    assert issue["deprecated"] is True
    assert problem_error["deprecated_code"]["deprecated"] is True
    assert "code" in problem_error
