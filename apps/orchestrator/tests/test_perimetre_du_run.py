"""`scope_respected` juge ce que le RUN a écrit, pas le ticket entier (S22-11).

Sur le locataire dev, le 09/10, l'ADR de l'étude #5 rougissait sur « 3 file(s) outside the allowed
paths » : le Makefile et deux fichiers qu'avait poussés l'étape de CADRAGE, mesurés depuis `main`.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio

DEPOT = "varga/billing-api"
BRANCHE = "choregos/123"
REGLE = {"name": "scope_respected", "params": {"paths": ["docs/adr/**"]}}


async def _run_qui_commence_a(setup: Any, depart: str | None) -> str:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_contracts import StageResult, StageStatus

    resultat = StageResult(status=StageStatus.DONE, summary="ADR rédigé")
    resultat.artifacts.branch = BRANCHE
    if depart:
        resultat.artifacts.reports["start_commit"] = depart
    async with session_scope() as session:
        run = Run(
            id=f"{setup.work_item_id}-t-draft-1",
            work_item_id=setup.work_item_id,
            project_id=setup.project_id,
            transition_id="t-draft",
            stage_role="architect",
            attempt=1,
            status="succeeded",
            result=resultat.model_dump(mode="json", by_alias=True),
        )
        session.add(run)
    return run.id


async def test_le_travail_des_etapes_d_avant_ne_compte_pas(setup: Any) -> None:
    from choregos_orchestrator.activities.gates import evaluate_gates

    setup.adapters.scm.set_diff(
        DEPOT,
        "main",
        BRANCHE,
        [("Makefile", 4, 0), ("sitecustomize.py", 3, 0), ("docs/adr/0001-x.md", 30, 0)],
    )
    setup.adapters.scm.set_diff(DEPOT, "abc123", BRANCHE, [("docs/adr/0001-x.md", 30, 0)])
    run_id = await _run_qui_commence_a(setup, "abc123")

    charge = {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "run_id": run_id,
        "gates": [REGLE],
    }
    [verdict] = await evaluate_gates(charge)
    assert verdict["passed"] is True, verdict


async def test_ce_que_le_run_ecrit_hors_perimetre_compte_toujours(setup: Any) -> None:
    from choregos_orchestrator.activities.gates import evaluate_gates

    setup.adapters.scm.set_diff(DEPOT, "abc123", BRANCHE, [("Makefile", 4, 0), ("docs/adr/0001-x.md", 30, 0)])
    run_id = await _run_qui_commence_a(setup, "abc123")
    charge = {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "run_id": run_id,
        "gates": [REGLE],
    }
    [verdict] = await evaluate_gates(charge)
    assert verdict["passed"] is False and verdict["annotations"] == ["Makefile"], verdict


async def test_un_run_qui_ne_dit_pas_d_ou_il_part_est_juge_sur_le_ticket(setup: Any) -> None:
    """Un runner d'avant S22-11 : rien ne change, le diff du ticket vaut."""
    from choregos_orchestrator.activities.gates import evaluate_gates

    setup.adapters.scm.set_diff(DEPOT, "main", BRANCHE, [("Makefile", 4, 0), ("docs/adr/0001-x.md", 30, 0)])
    run_id = await _run_qui_commence_a(setup, None)
    charge = {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "run_id": run_id,
        "gates": [REGLE],
    }
    [verdict] = await evaluate_gates(charge)
    assert verdict["passed"] is False, verdict
