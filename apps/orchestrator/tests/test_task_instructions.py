"""A task run of an assistant's installation renders its `config` and `preferences` (#1784, ADR 0137).

The stage asks the API for the values of the work item (`task_instruction_values`: the installation's
config, the subscriber's profile memory; `None` for any other work item) and renders the agent's
instructions with them: the model reads the installation's values, never the publication samples.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import Fixture
from .kits.registry_agent import agent_stage_plan, install_agent

TEMPLATE = (
    "Serve {{ config.house_name }} for {{ ticket.key }}; risk {{ preferences.risk_tolerance }}."
    "{{ config.hr_contact | default(' No HR contact.') }}"
)


async def test_a_task_run_sends_the_installations_values_to_the_model(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_orchestrator.activities import stage

    asked: list[str] = []

    async def values(session: Any, item: Any) -> dict[str, Any]:
        asked.append(item.id)
        return {"config": {"house_name": "Globex"}, "preferences": {"risk_tolerance": "low"}}

    monkeypatch.setattr(stage, "task_instruction_values", values)
    await install_agent(setup, instructions=TEMPLATE)
    prompt = (await stage.prepare_stage(agent_stage_plan(setup, agent="coordinateur")))["stage_input"][
        "playbook"
    ]["prompt"]

    assert asked == [setup.work_item_id]
    assert "Serve Globex for " in prompt and "; risk low. No HR contact." in prompt
    assert "<house_name>" not in prompt and "<risk_tolerance>" not in prompt


async def test_any_other_run_renders_as_before(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await install_agent(setup, instructions=TEMPLATE)
    prompt = (await prepare_stage(agent_stage_plan(setup, agent="coordinateur")))["stage_input"]["playbook"][
        "prompt"
    ]

    assert "Serve <house_name> for " in prompt, "no installation: the publication samples, as ever"
