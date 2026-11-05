"""Issue codes are English only: the former spelling and its `deprecated_code` are gone since 0.24.0.

S34-03 and S45-02 renamed the French issue codes and emitted the former spelling in
`deprecated_code` for one release; the last window (`role.playbook_not_found`) closed with 0.23.0
(S45-49, ADR 0098).
"""

from __future__ import annotations

import importlib

import choregos_contracts as contracts
import pytest


def test_the_issue_code_alias_module_is_gone() -> None:
    with pytest.raises(ImportError):
        importlib.import_module("choregos_contracts.issue_codes")


def test_the_contract_no_longer_declares_a_former_code() -> None:
    schemas = contracts.load_openapi()["components"]["schemas"]
    assert "deprecated_code" not in schemas["WorkflowIssue"]["properties"]
    problem_error = schemas["Problem"]["properties"]["errors"]["items"]["properties"]
    assert "deprecated_code" not in problem_error
    assert "code" in problem_error
    raw = str(contracts.load_openapi())
    assert "deprecated_code" not in raw
    assert "playbook_introuvable" not in raw  # lang: fr-ok - removed, asserted gone
