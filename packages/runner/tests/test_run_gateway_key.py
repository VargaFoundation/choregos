"""The runner reads its gateway key from the run's Secret, not from its input (#523).

The executor puts the key in the run's Secret, next to the token, as `CHOREGOS_RUN_GATEWAY_KEY`;
the orchestrator no longer stores it with the input. A runner started by an orchestrator older than
#523 has no such variable: it falls back on the key its input carries — kept for one release.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_contracts import StageInput
from choregos_runner.config import RunnerSettings
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner, RunOutcome

from .conftest import FakeInternalClient


async def _key_given_to_the_agent(settings: RunnerSettings, stage_input: StageInput) -> str | None:
    """What `Runner.run` hands to the stage: the input it fetched, with the key it settled on."""
    seen: list[StageInput] = []

    class _Recording(Runner):
        async def execute(self, stage_input: StageInput, client: Any) -> RunOutcome:
            seen.append(stage_input)
            return RunOutcome(Exit.OK)

    await _Recording(settings, FakeInternalClient(stage_input)).run()  # type: ignore[arg-type]
    return seen[0].gateway_key


async def test_the_key_comes_from_the_run_secret_when_the_input_has_none(
    stage_input: StageInput, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_RUN_GATEWAY_KEY", "sk-from-the-secret")
    settings = RunnerSettings()
    assert settings.gateway_key == "sk-from-the-secret"

    without_key = stage_input.model_copy(update={"gateway_key": None})
    assert await _key_given_to_the_agent(settings, without_key) == "sk-from-the-secret"


async def test_the_secret_wins_over_a_key_left_in_the_input(
    stage_input: StageInput, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_RUN_GATEWAY_KEY", "sk-from-the-secret")
    old = stage_input.model_copy(update={"gateway_key": "sk-from-the-row"})
    assert await _key_given_to_the_agent(RunnerSettings(), old) == "sk-from-the-secret"


async def test_without_the_variable_the_input_key_is_used_as_before(
    stage_input: StageInput, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An orchestrator older than #523: its runner still works, with the key of its input."""
    monkeypatch.delenv("CHOREGOS_RUN_GATEWAY_KEY", raising=False)
    settings = RunnerSettings()
    assert settings.gateway_key == ""
    old = stage_input.model_copy(update={"gateway_key": "sk-from-the-row"})
    assert await _key_given_to_the_agent(settings, old) == "sk-from-the-row"
