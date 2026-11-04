"""The run's tool scripts: the deployment's kill switch, the organisation's ceiling, the policy and the
role (S57-05, ADR 0122 §6). Off is never sent: a runner older than ADR 0122 refuses `tools.scripts`."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from choregos_contracts import StageInput, ToolScripts, ToolsPolicy
from choregos_orchestrator.activities import evals
from choregos_orchestrator.activities import stage as stage_activities
from choregos_orchestrator.config import reset_settings_cache
from choregos_orchestrator.context_reduction import resolve_tool_scripts, stage_input_payload

from .conftest import Fixture

OFFERED = {"scripts": "offer", "per_role": {"review": {"scripts": "off"}}}


@pytest.fixture
def switch_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The deployment lets the policy decide (`CHOREGOS_TOOL_SCRIPTS=on`); off is its default."""
    monkeypatch.setenv("CHOREGOS_TOOL_SCRIPTS", "on")
    reset_settings_cache()
    yield
    monkeypatch.delenv("CHOREGOS_TOOL_SCRIPTS")
    reset_settings_cache()


async def _set_tools(project_id: str, tools: dict[str, Any] | None) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, project_id)
        assert row is not None
        model = policy_model(row).model_dump(mode="json", exclude_none=True)
        model.pop("tools", None)
        if tools is not None:
            model["tools"] = tools
        row.json_doc = model


async def _set_ceiling(project_id: str, ceiling: dict[str, Any]) -> None:
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope
    from choregos_api.services.definitions import CEILING_KEY

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        organisation = await session.get(Organization, project.org_id)
        assert organisation is not None
        organisation.settings = {**(organisation.settings or {}), CEILING_KEY: {"ceiling": ceiling}}


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


async def _tools_of(setup: Fixture, role: str) -> dict[str, Any]:
    prepared = await stage_activities.prepare_stage(_plan(setup, role))
    tools = prepared["stage_input"]["tools"]
    assert isinstance(tools, dict)
    return tools


# ───────────────────────── the resolver ─────────────────────────


def test_the_kill_switch_gives_none_whatever_the_policy() -> None:
    offered = ToolsPolicy.model_validate({"scripts": "offer"})
    assert resolve_tool_scripts(offered, "implement", allowed_by_deployment=False) is None
    assert resolve_tool_scripts(offered, "implement", allowed_by_deployment=True) == ToolScripts(
        max_concurrent_calls=4
    )


def test_a_role_turned_off_gets_none_and_the_others_the_project_s_bound() -> None:
    tools = ToolsPolicy.model_validate(
        {**OFFERED, "per_role": {**OFFERED["per_role"], "triage": {"max_concurrent_calls": 2}}}
    )
    assert resolve_tool_scripts(tools, "review", allowed_by_deployment=True) is None
    assert resolve_tool_scripts(tools, "implement", allowed_by_deployment=True) == ToolScripts(
        max_concurrent_calls=4
    )
    assert resolve_tool_scripts(tools, "triage", allowed_by_deployment=True) == ToolScripts(
        max_concurrent_calls=2
    )
    assert resolve_tool_scripts(ToolsPolicy(), "implement", allowed_by_deployment=True) is None


def test_an_inactive_setting_is_absent_from_the_payload_an_active_one_present(
    stage_input_example: dict[str, Any],
) -> None:
    stage_input = StageInput.model_validate(stage_input_example)
    assert stage_input.tools.scripts is None
    payload = stage_input_payload(stage_input)
    assert "scripts" not in payload["tools"], "an older runner refuses the key"
    assert "mcp" in payload["tools"], "the rest of `tools` is sent as before"
    stage_input.tools.scripts = ToolScripts(max_concurrent_calls=3)
    assert stage_input_payload(stage_input)["tools"]["scripts"] == {"max_concurrent_calls": 3}


@pytest.fixture
def stage_input_example() -> dict[str, Any]:
    import json

    import choregos_contracts as contracts

    with (contracts.schemas_dir() / "examples" / "stage-input.example.json").open(encoding="utf-8") as fh:
        loaded: dict[str, Any] = json.load(fh)
    return loaded


# ───────────────────────── prepare_stage ─────────────────────────


async def test_a_policy_without_tools_sends_none(setup: Fixture, switch_on: None) -> None:
    await _set_tools(setup.project_id, None)
    assert "scripts" not in await _tools_of(setup, "implement")


async def test_the_policy_and_its_role_override_reach_the_stage_input(
    setup: Fixture, switch_on: None
) -> None:
    await _set_tools(setup.project_id, OFFERED)
    assert (await _tools_of(setup, "implement"))["scripts"] == {"max_concurrent_calls": 4}
    assert "scripts" not in await _tools_of(setup, "review"), "`per_role: {review: {scripts: off}}`"


async def test_the_kill_switch_is_off_by_default_and_wins_over_the_policy(setup: Fixture) -> None:
    await _set_tools(setup.project_id, OFFERED)
    assert "scripts" not in await _tools_of(setup, "implement")


async def test_the_organisation_s_ceiling_forbids_them(setup: Fixture, switch_on: None) -> None:
    """A policy saved before the ceiling: the policy that runs is clamped to it (S27-05)."""
    await _set_tools(setup.project_id, {"scripts": "offer", "per_role": {"triage": {"scripts": "offer"}}})
    await _set_ceiling(setup.project_id, {"tools": {"scripts": "off"}})
    assert "scripts" not in await _tools_of(setup, "implement")
    assert "scripts" not in await _tools_of(setup, "triage"), "a role's own `offer` is clamped too"


async def test_an_eval_cell_runs_the_mcp_door_only(setup: Fixture, switch_on: None) -> None:
    """Until the matrix measures the tool bridge (S57-09), a cell is the `mcp` cell, whatever the policy."""
    await _set_tools(setup.project_id, OFFERED)
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, setup.project_id)
        assert row is not None
        model = policy_model(row).model_dump(mode="json", exclude_none=True)
        model["memory"] = {**model.get("memory", {}), "enabled": False}
        row.json_doc = model
    seeded = await evals.seed_eval_ticket(
        {
            "project_slug": "billing-api",
            "matrix_id": "evals-nightly",
            "backend": "claude-code",
            "model": "platform/standard",
            "with_memory": False,
            "context": "none",
            "fixture": "py-rounding-bug",
            "role": "implement",
        }
    )
    assert not seeded["skipped"], seeded["reason"]
    prepared = await stage_activities.prepare_stage(seeded["plan"])
    assert "scripts" not in prepared["stage_input"]["tools"]
