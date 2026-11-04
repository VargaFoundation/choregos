"""A business step reads what the previous one produced (HR bench of 2026-09-24)."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio


async def test_the_qualification_prompt_contains_the_sourcing_profiles(setup: Any, tmp_path: Any) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_contracts import ContextPack
    from choregos_orchestrator.activities.base import project_bundle
    from choregos_orchestrator.activities.stage import StagePlan, _render_playbook

    # the qualification playbook comes from the deployment (as in demo/), with `{{ inputs.profiles }}`
    (tmp_path / "qualification.md").write_text(
        "You qualify.\n\n## The profiles to qualify\n{{ inputs.profiles }}\n", encoding="utf-8"
    )
    import os

    from choregos_playbooks import _env

    # the Jinja loader is cached with its directories: clear it on both sides
    _env.cache_clear()
    os.environ["CHOREGOS_PLAYBOOKS_DIR"] = str(tmp_path)
    try:
        async with session_scope(orgs="*") as session:
            bundle = await project_bundle(session, setup.project_id)
            item = await session.get(WorkItem, setup.work_item_id)
            assert item is not None
            item.documents = {"profiles": "## Three profiles\n- Aline\n- Bakary\n- Chloé"}
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
                inputs=["profiles"],
            )
            prompt = _render_playbook(plan, bundle, item, ContextPack.empty("q"))
    finally:
        os.environ.pop("CHOREGOS_PLAYBOOKS_DIR", None)
        _env.cache_clear()
    assert "Bakary" in prompt, "the profile produced by sourcing must be in the qualification prompt"
