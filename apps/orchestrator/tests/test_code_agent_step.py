"""An agent written in code (ADR 0063) runs as the `code` backend, in the image the operator named."""

from __future__ import annotations

import json
from typing import Any

import pytest
from choregos_orchestrator.activities import stage as stage_activities
from temporalio.exceptions import ApplicationError

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

TRIAGER = "registry.example/agents/ticket-triager@sha256:" + "b" * 64
COMMAND = ["python", "-m", "ticket_triager"]


@pytest.fixture
def images(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_orchestrator.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_AGENT_IMAGES", json.dumps({"ticket-triager": TRIAGER}))
    reset_settings_cache()


async def _agent(setup: Fixture, runtime: dict[str, Any] | None, backend: str | None = None) -> None:
    from choregos_api.db.models import Agent, AgentVersion, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        agent = Agent(
            org_id=project.org_id,
            slug="triager",
            kind="internal",
            display_name="Triager",
            status="active",
            lifecycle="active",
        )
        session.add(agent)
        await session.flush()
        spec: dict[str, Any] = {
            "instructions": "Triage {{ ticket.title }}.",
            "model": "profile:standard",
            "limits": {"max_turns": 9, "max_minutes": 20},
            "budget": {"run_usd": 0.5, "daily_usd": 5.0},
            "skills": [],
            "mcp_servers": [],
        }
        if runtime is not None:
            spec["runtime"] = runtime
        if backend is not None:
            spec["backend"] = backend
        session.add(
            AgentVersion(agent_id=agent.id, org_id=project.org_id, version=1, spec=spec, checksum="sha256:v1")
        )


def _plan(setup: Fixture, **extra: Any) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": "t-implement",
        "role": "implement",
        "from_state": "ready",
        "to_state": "done",
        "actor": "dev",
        "attempt": 1,
        "agent": "triager",
        **extra,
    }


def _code(image: str = "ticket-triager", api_format: str = "openai") -> dict[str, Any]:
    return {"kind": "code", "image": image, "command": COMMAND, "api_format": api_format}


async def test_a_code_agent_yields_the_code_backend_its_command_and_its_image(
    setup: Fixture, images: None
) -> None:
    await _agent(setup, _code())
    prepared = await stage_activities.prepare_stage(_plan(setup))
    agent = prepared["stage_input"]["agent"]
    assert agent["backend"] == "code"
    assert agent["launch"]["command"] == COMMAND
    assert agent["image"] == TRIAGER, "the reference the operator mapped, not the name"
    assert prepared["stage_input"]["model"]["api_format"] == "openai"
    assert prepared["stage_input"]["budget"]["max_turns"] == 9


async def test_the_pod_runs_the_agents_image(setup: Fixture, images: None) -> None:
    await _agent(setup, _code())
    prepared = await stage_activities.prepare_stage(_plan(setup))
    await stage_activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )
    assert setup.adapters.executor.started[-1].runner_image == TRIAGER


async def test_an_anthropic_runtime_sets_the_models_api_format(setup: Fixture, images: None) -> None:
    await _agent(setup, _code(api_format="anthropic"))
    prepared = await stage_activities.prepare_stage(_plan(setup))
    assert prepared["stage_input"]["model"]["api_format"] == "anthropic"


async def test_an_agent_without_a_runtime_keeps_the_runner_image(setup: Fixture, images: None) -> None:
    from choregos_orchestrator.config import get_settings

    await _agent(setup, None)
    prepared = await stage_activities.prepare_stage(_plan(setup))
    assert prepared["stage_input"]["agent"]["image"] is None
    await stage_activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )
    assert setup.adapters.executor.started[-1].runner_image == get_settings().runner_image


async def test_an_image_the_operator_did_not_name_stops_the_step_before_any_key(
    setup: Fixture, images: None
) -> None:
    await _agent(setup, _code(image="miner"))
    with pytest.raises(ApplicationError) as raised:
        await stage_activities.prepare_stage(_plan(setup))
    assert raised.value.type == "agent_image_unknown" and raised.value.non_retryable
    assert "ticket-triager" in str(raised.value), "the known names are listed"
    assert setup.adapters.gateway.keys == {}


async def test_an_actor_backend_that_contradicts_the_runtime_is_refused(setup: Fixture, images: None) -> None:
    await _agent(setup, _code())
    with pytest.raises(ApplicationError) as raised:
        await stage_activities.prepare_stage(_plan(setup, backend="codex"))
    assert raised.value.type == "AgentIndisponible" and raised.value.non_retryable
    assert "written in code" in str(raised.value)


async def test_an_acp_runtime_names_the_backend(setup: Fixture, images: None) -> None:
    from .test_backend_toggle_enforced import _switch

    await _switch("opencode", enabled=True)  # S42-07c: only `claude-code` is on by default
    await _agent(setup, {"kind": "acp", "backend": "opencode"})
    prepared = await stage_activities.prepare_stage(_plan(setup))
    assert prepared["stage_input"]["agent"]["backend"] == "opencode"


async def test_a_prompt_agent_is_carried_by_the_runners_own_loop(setup: Fixture, images: None) -> None:
    await _agent(setup, {"kind": "prompt", "tool_choice": "required", "response_format": "json_object"})
    prepared = await stage_activities.prepare_stage(_plan(setup))
    stage_input = prepared["stage_input"]
    assert stage_input["agent"]["backend"] == "prompt" and stage_input["agent"]["image"] is None
    assert stage_input["prompt"]["tool_choice"] == "required"
    assert stage_input["prompt"]["response_format"] == "json_object"
    assert stage_input["budget"]["max_turns"] == 9
    assert stage_input["model"]["api_format"] == "openai", "the loop speaks chat completions"


async def test_an_actor_backend_on_a_prompt_agent_is_refused(setup: Fixture, images: None) -> None:
    await _agent(setup, {"kind": "prompt"})
    with pytest.raises(ApplicationError) as raised:
        await stage_activities.prepare_stage(_plan(setup, backend="claude"))
    assert raised.value.type == "AgentIndisponible" and raised.value.non_retryable
    assert "prompt agent" in str(raised.value)
    assert setup.adapters.gateway.keys == {}


@pytest.mark.parametrize(
    ("runtime", "reason"),
    [
        ({"kind": "voice"}, "only as a session"),
    ],
)
async def test_a_runtime_no_step_can_carry_is_refused_before_any_key(
    setup: Fixture, images: None, runtime: dict[str, Any], reason: str
) -> None:
    await _agent(setup, runtime)
    with pytest.raises(ApplicationError) as raised:
        await stage_activities.prepare_stage(_plan(setup))
    assert raised.value.type == "AgentIndisponible" and raised.value.non_retryable
    assert f"`{runtime['kind']}` cannot run as a workflow step" in str(raised.value)
    assert reason in str(raised.value)
    assert setup.adapters.gateway.keys == {}
