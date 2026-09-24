"""`tool_called` lit les appels que la plateforme a comptés — le registre, pas le résultat."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio


async def test_la_garantie_voit_l_appel_au_registre(setup: Any) -> None:
    from choregos_api.db.models import CostLedger, Run
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from choregos_orchestrator.activities.gates import evaluate_gates

    async with session_scope(orgs="*") as session:
        run = Run(
            id="varga-billing-api-123-t-sourcing-1",
            work_item_id=setup.work_item_id,
            project_id=setup.project_id,
            transition_id="t-sourcing",
            stage_role="sourcing",
            attempt=1,
            actor="agent",
            backend="claude-code",
            model="platform/standard",
            status="succeeded",
            stage_input={},
            result={
                "schema": "choregos/StageResult/v1",
                "status": "done",
                "summary": "ok",
                "evidence": {"facts": {"lieu_verifie": True}},
            },
        )
        session.add(run)
    charge = {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "run_id": run.id,
        "gates": [{"name": "tool_called", "params": {"tools": ["verifier_adresse"]}}],
    }
    # l'agent RACONTE `lieu_verifie` ; le registre n'a rien
    [refus] = await evaluate_gates(charge)
    assert refus["passed"] is False and "verifier_adresse" in refus["detail"]

    async with session_scope(orgs="*") as session:
        session.add(
            CostLedger(
                project_id=setup.project_id,
                work_item_id=setup.work_item_id,
                run_id=run.id,
                provider="adresse.data.gouv.fr",
                model="verifier_adresse",
                backend="claude-code",
                stage_role="sourcing",
                size="M",
                tokens_in=0,
                tokens_out=0,
                tokens_cached=0,
                cost_usd=0,
                cost_eur=0,
                fx_rate=0.92,
                ts=utcnow(),
                kind="tool",
            )
        )
    [ok] = await evaluate_gates(charge)
    assert ok["passed"] is True
