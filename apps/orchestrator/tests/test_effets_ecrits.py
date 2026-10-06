"""Ce que la plateforme fait s'écrit (#175) : `does: open_pr` ouvre la PR, `does: merge_pr` la fusionne,
quel que soit le nom de l'état visé. Avant, c'étaient les préfixes `pr_` et `merged` du nom : renommer
l'état depuis la console retirait l'effet sans le dire. Ici `pr_open` devient `revue` et `merged`
devient `fusionnee` par l'édition typée ; la PR s'ouvre et se fusionne encore.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import Fixture, stage_result
from .test_interpreter import _wait_state, scripted, start

pytestmark = pytest.mark.asyncio


async def _publier(setup: Fixture, texte: str) -> None:
    """Le workflow actif du projet devient `texte` — ce que ferait une publication."""
    from choregos_api.db.models import WorkflowDef
    from choregos_api.db.session import session_scope
    from choregos_core import checksum, parse_workflow
    from sqlalchemy import select

    wf, rapport = parse_workflow(texte, strict=False)
    assert rapport.valid, [e.message for e in rapport.errors]
    async with session_scope() as session:
        definition = (
            await session.execute(
                select(WorkflowDef).where(WorkflowDef.project_id == setup.project_id, WorkflowDef.is_active)
            )
        ).scalar_one()
        definition.yaml = texte
        definition.json_doc = wf.model_dump(mode="json", by_alias=True, exclude_none=True)
        definition.checksum = checksum(wf)


async def test_des_etats_renommes_ouvrent_et_fusionnent_encore_la_pr(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_core.dsl import template_yaml
    from choregos_core.dsl.edition import editer

    from .test_interpreter import _pr_ref

    renomme = editer(
        template_yaml("default-simple"),
        [
            {"op": "rename_state", "from": "pr_open", "to": "revue"},
            {"op": "rename_state", "from": "merged", "to": "fusionnee"},
        ],
    )
    assert "pr_open" not in renomme.yaml and "merged:" not in renomme.yaml
    await _publier(setup, renomme.yaml)
    scripted(
        setup.adapters,
        stage_result("spec rédigée", outputs={"size": "M", "risk": "low", "spec_markdown": "## Spec"}),
        stage_result("implémenté", artifacts={"branch": "choregos/123", "commits": ["fix(orders): avoirs"]}),
        stage_result("vérifié"),
    )
    setup.adapters.cd.set_health("billing-api", "Healthy")
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal(
            "human_decision", {"approved": True, "decided_by": "augustin", "kind": "approval"}
        )
        await _wait_state(handle, "revue")
        assert setup.adapters.scm.prs, "`does: open_pr` a ouvert la PR — l'état ne s'appelle plus `pr_…`"
        assert not setup.adapters.scm.merge_queue

        setup.adapters.scm.set_checks(_pr_ref(setup), "success")
        setup.adapters.scm.submit_review(_pr_ref(setup), "marie", "approved")
        for numero, evenement in enumerate(("scm.check.completed", "scm.pr.review_submitted")):
            charge = {"conclusion": "success"} if numero == 0 else {"state": "approved"}
            await handle.signal(
                "inbound",
                {"type": evenement, "source": "github", "delivery_id": f"d{numero}", "payload": charge},
            )
        await _wait_state(handle, "fusionnee")
        pr = _pr_ref(setup)
        assert [(r.repo, r.number) for r in setup.adapters.scm.merge_queue] == [(pr.repo, pr.number)], (
            "`does: merge_pr` a mis la PR en file de fusion — l'état ne s'appelle plus `merged…`"
        )
        await handle.terminate("fin du test")
