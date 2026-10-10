"""Ce que le prompt d'un run embarque des étapes d'avant laisse un reçu « lue » (S25-04).

L'étape suivante lit la spec d'abord dans son prompt, rendu à la création du run : c'est là qu'il
faut dire QUELLE révision elle a reçue. Une activité rejouée ne double pas le reçu (ADR 0008).
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio

SPEC = "# Spec\n\nDeduct credit notes from the total, rounded half to even.\n"


async def _preparer(setup: Any) -> dict[str, Any]:
    from choregos_orchestrator.activities import stage as activites

    return await activites.prepare_stage(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "transition_id": "t-implement",
            "role": "implement",
            "actor": "dev",
            "from_state": "ready",
            "to_state": "in_progress",
            "attempt": 1,
        }
    )


async def _recus(run_id: str) -> list[tuple[str, str, str]]:
    from choregos_api.db.models import HandOffReceipt
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        lignes = (
            await session.execute(select(HandOffReceipt).where(HandOffReceipt.run_id == run_id))
        ).scalars()
        return [(r.output, r.kind, r.digest) for r in lignes]


async def test_le_prompt_d_implementation_lit_la_revision_de_la_spec(setup: Any) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.recus import empreinte

    async with session_scope(orgs="*") as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.documents = {"spec_markdown": SPEC}

    plan = await _preparer(setup)
    run_id = str(plan["run_id"])
    assert SPEC in plan["stage_input"]["playbook"]["prompt"]
    assert await _recus(run_id) == [("spec_markdown", "read", empreinte(SPEC))]

    # L'activité rejouée sur le même run ne fait pas de second reçu.
    await _preparer(setup)
    assert await _recus(run_id) == [("spec_markdown", "read", empreinte(SPEC))]


async def test_sans_spec_le_prompt_ne_lit_rien(setup: Any) -> None:
    plan = await _preparer(setup)
    assert await _recus(str(plan["run_id"])) == []
