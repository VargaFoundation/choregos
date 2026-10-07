"""`markdown_sections` lit ce que la branche ÉCRIT : le diff, pas le récit de l'agent (S21-20)."""

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
REGLE = {
    "name": "markdown_sections",
    "params": {
        "paths": ["docs/adr/[0-9][0-9][0-9][0-9]-*.md"],
        "sections": ["Context and Problem Statement", "Considered Options", "Decision Outcome"],
    },
}


def test_texte_ajoute_garde_les_lignes_plus_sans_leur_signe() -> None:
    from choregos_orchestrator.activities.gates import texte_ajoute

    patch = "--- a/x.md\n+++ b/x.md\n@@ -1,2 +1,3 @@\n # Title\n-old line\n+new line\n+## Decision Outcome\n"
    assert texte_ajoute(patch) == "new line\n## Decision Outcome"


async def test_le_texte_ajoute_du_diff_parvient_a_la_garantie(setup: Any) -> None:
    from choregos_orchestrator.activities.gates import evaluate_gates

    charge = {"project_id": setup.project_id, "work_item_id": setup.work_item_id, "gates": [REGLE]}
    patch_complet = "\n".join(f"+{ligne}" for ligne in ADR.splitlines())
    setup.adapters.scm.set_diff(
        "varga/billing-api", "main", "choregos/123", [("docs/adr/0042-rls.md", 16, 0, patch_complet)]
    )
    [ok] = await evaluate_gates(charge)
    assert ok["passed"] is True, ok["detail"]

    sans_decision = "\n".join(
        f"+{ligne}" for ligne in ADR.split("## Decision Outcome", maxsplit=1)[0].splitlines()
    )
    setup.adapters.scm.set_diff(
        "varga/billing-api", "main", "choregos/123", [("docs/adr/0042-rls.md", 9, 0, sans_decision)]
    )
    [refus] = await evaluate_gates(charge)
    assert refus["passed"] is False and "Decision Outcome" in refus["detail"]
