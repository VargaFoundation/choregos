"""An agent's author chooses the language its output is written in (ADR 0126).

The frame a registry agent's instructions run under asks for English by default. An organisation
whose people work in another language publishes an agent with `language`, and the run's prompt asks
for that language instead, while what a machine reads stays as the contract writes it.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

ENGLISH = "Write in English everything a person will read"
FRENCH = "Write in French everything a person will read"
MACHINE = "the enum values of the result contract (`status`, `type`, `severity`)"


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
        **extra,
    }


async def _agent(setup: Fixture, **spec: Any) -> None:
    from choregos_api.db.models import Agent, AgentVersion, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        agent = Agent(
            org_id=project.org_id,
            slug="assistant",
            kind="internal",
            display_name="assistant",
            status="active",
            scope="org",
        )
        session.add(agent)
        await session.flush()
        body = {"instructions": "Answer the request.", "model": "profile:standard", **spec}
        session.add(
            AgentVersion(agent_id=agent.id, org_id=project.org_id, version=1, spec=body, checksum="sha256:v1")
        )


async def _prompt(setup: Fixture, **extra: Any) -> str:
    from choregos_orchestrator.activities.stage import prepare_stage

    stage_input = (await prepare_stage(_plan(setup, **extra)))["stage_input"]
    return str(stage_input["playbook"]["prompt"])


async def test_an_agent_that_writes_in_french_is_asked_to_in_its_frame(setup: Fixture) -> None:
    await _agent(setup, language="fr")
    prompt = await _prompt(setup, agent="assistant")
    assert prompt.startswith("Answer the request.")
    assert FRENCH in prompt and MACHINE in prompt
    assert ENGLISH not in prompt, "one language, not two"


async def test_an_agent_without_a_language_writes_in_english_as_before(setup: Fixture) -> None:
    await _agent(setup)
    prompt = await _prompt(setup, agent="assistant")
    assert ENGLISH in prompt and FRENCH not in prompt


async def test_an_agent_without_instructions_runs_its_role_in_its_language(setup: Fixture) -> None:
    """Its role's playbook stands in for the instructions; the language is still the author's."""
    await _agent(setup, instructions="", language="fr")
    prompt = await _prompt(setup, agent="assistant")
    assert FRENCH in prompt and ENGLISH not in prompt


async def test_a_trial_runs_in_the_drafts_language(setup: Fixture) -> None:
    draft = {"instructions": "Try me.", "language": "pt-BR"}
    prompt = await _prompt(setup, trial_spec=draft, trial_budget_usd=0.5)
    assert "Write in Brazilian Portuguese everything a person will read" in prompt


async def test_a_built_in_role_writes_in_english(setup: Fixture) -> None:
    prompt = await _prompt(setup)
    assert ENGLISH in prompt and "Write in French" not in prompt
