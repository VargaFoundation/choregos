"""The run's context reduction: the policy, the role, then the deployment's kill switch (S37-03,
ADR 0055)."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_orchestrator.activities import stage as stage_activities
from choregos_orchestrator.config import reset_settings_cache

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

BOTH_ON = {
    "shell_filter": "rtk",
    "compression": "headroom",
    "rtk_exclude_commands": ["git diff"],
    "per_role": {"review": {"compression": "none"}},
}


async def _set_context(project_id: str, context: dict[str, Any] | None) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, project_id)
        assert row is not None
        model = policy_model(row).model_dump(mode="json", exclude_none=True)
        model.pop("context", None)
        if context is not None:
            model["context"] = context
        row.json_doc = model


def _plan(setup: Fixture, role: str) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": f"t-{role}",
        "role": role,
        "from_state": "in_progress",
        "to_state": "verifying",
        "actor": "dev",
        "attempt": 1,
        "backend": "claude-code",
        "model_request": "profile:by_size",
    }


async def test_a_policy_without_context_sends_none(setup: Fixture) -> None:
    await _set_context(setup.project_id, None)
    prepared = await stage_activities.prepare_stage(_plan(setup, "implement"))
    assert "context" not in prepared["stage_input"]


async def test_the_policy_and_its_role_override_reach_the_stage_input(setup: Fixture) -> None:
    await _set_context(setup.project_id, BOTH_ON)
    implement = (await stage_activities.prepare_stage(_plan(setup, "implement")))["stage_input"]
    assert implement["context"]["shell_filter"] == "rtk"
    assert implement["context"]["compression"] == "headroom"
    assert implement["context"]["rtk_exclude_commands"] == ["git diff"]
    review = (await stage_activities.prepare_stage(_plan(setup, "review")))["stage_input"]
    assert (review["context"]["shell_filter"], review["context"]["compression"]) == ("rtk", "none")


async def test_the_kill_switch_wins_over_the_policy(setup: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    await _set_context(setup.project_id, BOTH_ON)
    monkeypatch.setenv("CHOREGOS_CONTEXT_REDUCTION", "off")
    reset_settings_cache()
    try:
        prepared = await stage_activities.prepare_stage(_plan(setup, "implement"))
    finally:
        monkeypatch.delenv("CHOREGOS_CONTEXT_REDUCTION")
        reset_settings_cache()
    assert "context" not in prepared["stage_input"]
