"""Une étape métier lit ce que la précédente a produit (banc RH du 2026-09-24)."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio


async def test_le_prompt_de_qualification_contient_les_profils_du_sourcing(setup: Any, tmp_path: Any) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_contracts import ContextPack
    from choregos_orchestrator.activities.base import project_bundle
    from choregos_orchestrator.activities.stage import StagePlan, _render_playbook

    # le playbook de qualification vient du déploiement (comme dans demo/), avec `{{ inputs.profils }}`
    (tmp_path / "qualification.md").write_text(
        "Tu qualifies.\n\n## Les profils à qualifier\n{{ inputs.profils }}\n", encoding="utf-8"
    )
    import os

    from choregos_playbooks import _env

    # le chargeur Jinja est mis en cache avec ses répertoires : on le vide de part et d'autre
    _env.cache_clear()
    os.environ["CHOREGOS_PLAYBOOKS_DIR"] = str(tmp_path)
    try:
        async with session_scope(orgs="*") as session:
            bundle = await project_bundle(session, setup.project_id)
            item = await session.get(WorkItem, setup.work_item_id)
            assert item is not None
            item.documents = {"profils": "## Trois profils\n- Aline\n- Bakary\n- Chloé"}
            plan = StagePlan(
                project_id=setup.project_id,
                work_item_id=setup.work_item_id,
                transition_id="t-qualification",
                role="qualification",
                from_state="sourcing",
                to_state="qualification",
                actor="evaluateur",
                attempt=1,
                backend="claude-code",
                model_request="profile:standard",
                fresh_context=True,
                max_turns=5,
                max_minutes=5,
                playbook="qualification",
                outputs=["evaluation"],
                inputs=["profils"],
            )
            prompt = _render_playbook(plan, bundle, item, ContextPack.empty("q"))
    finally:
        os.environ.pop("CHOREGOS_PLAYBOOKS_DIR", None)
        _env.cache_clear()
    assert "Bakary" in prompt, "le profil produit par le sourcing doit être dans le prompt de qualification"
