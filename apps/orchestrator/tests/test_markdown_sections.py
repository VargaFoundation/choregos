"""`markdown_sections` reads what the branch WRITES: the diff, not the agent's account (S21-20)."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio

ADR = """# Use row-level security

## Context and Problem Statement

Tenants share one database.

## Considered Options

* Row-level security
* One schema per tenant

## Decision Outcome

Chosen option: "Row-level security", because it keeps one schema.
"""
RULE = {
    "name": "markdown_sections",
    "params": {
        "paths": ["docs/adr/[0-9][0-9][0-9][0-9]-*.md"],
        "sections": ["Context and Problem Statement", "Considered Options", "Decision Outcome"],
    },
}


def test_added_text_keeps_the_plus_lines_without_their_sign() -> None:
    from choregos_orchestrator.activities.gates import added_text_of

    patch = "--- a/x.md\n+++ b/x.md\n@@ -1,2 +1,3 @@\n # Title\n-old line\n+new line\n+## Decision Outcome\n"
    assert added_text_of(patch) == "new line\n## Decision Outcome"


async def test_the_added_text_of_the_diff_reaches_the_gate(setup: Any) -> None:
    from choregos_orchestrator.activities.gates import evaluate_gates

    charge = {"project_id": setup.project_id, "work_item_id": setup.work_item_id, "gates": [RULE]}
    patch_complet = "\n".join(f"+{line}" for line in ADR.splitlines())
    setup.adapters.scm.set_diff(
        "varga/billing-api", "main", "choregos/123", [("docs/adr/0042-rls.md", 16, 0, patch_complet)]
    )
    [ok] = await evaluate_gates(charge)
    assert ok["passed"] is True, ok["detail"]

    sans_decision = "\n".join(
        f"+{line}" for line in ADR.split("## Decision Outcome", maxsplit=1)[0].splitlines()
    )
    setup.adapters.scm.set_diff(
        "varga/billing-api", "main", "choregos/123", [("docs/adr/0042-rls.md", 9, 0, sans_decision)]
    )
    [refusal] = await evaluate_gates(charge)
    assert refusal["passed"] is False and "Decision Outcome" in refusal["detail"]
