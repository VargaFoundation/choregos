"""The runner with the agent in another container (ADR 0146): the agent is started, and the project's
checks run, by the launcher, never as the runner's own child; with no launcher the run refuses."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from choregos_contracts import StageInput
from choregos_core.exit_codes import explanation
from choregos_runner import launcher as lc
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient, agent_script, free_port, valid_result


@pytest.fixture(autouse=True)
def _umask() -> Iterator[None]:
    old = os.umask(0o022)
    yield
    os.umask(old)


@pytest.fixture
async def serving() -> AsyncIterator[tuple[int, list[Any]]]:
    """A launcher in this process, recording each request it is asked to run."""
    port = free_port()
    asked: list[Any] = []
    real = asyncio.create_subprocess_exec

    async def recording(*argv: Any, **kwargs: Any) -> Any:
        asked.append((argv, kwargs.get("env")))
        return await real(*argv, **kwargs)

    server = await asyncio.start_server(lc.handle, "127.0.0.1", port)
    import choregos_runner.launcher as module

    module.asyncio.create_subprocess_exec = recording  # type: ignore[assignment]
    try:
        yield port, asked
    finally:
        module.asyncio.create_subprocess_exec = real  # type: ignore[assignment]
        server.close()
        await server.wait_closed()


def _settings(base: Any, **overrides: Any) -> Any:
    return base.model_copy(update={"agent_isolation": "container", "launcher_wait_seconds": 0.3, **overrides})


async def _execute(stage_input: StageInput, settings: Any) -> tuple[Any, FakeInternalClient, Runner]:
    agent_script(
        {"turns": [{"messages": ["ok"], "writes": [], "result": valid_result()}]},
        stage_input,
        settings.workspace,
    )
    client = FakeInternalClient(stage_input)
    runner = Runner(settings, client)  # type: ignore[arg-type]
    outcome = await runner.execute(stage_input, client)  # type: ignore[arg-type]
    return outcome, client, runner


async def test_without_a_launcher_the_run_refuses_and_the_agent_never_starts(
    stage_input: StageInput, runner_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[Any] = []
    real = asyncio.create_subprocess_exec

    async def watch(*argv: Any, **kwargs: Any) -> Any:
        started.append(argv)
        return await real(*argv, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", watch)
    settings = _settings(runner_settings, launcher_port=free_port())

    outcome, client, _ = await _execute(stage_input, settings)

    assert outcome.exit_code is Exit.AGENT_ISOLATION_UNAVAILABLE
    assert [a for a in started if "fake_agent" in " ".join(map(str, a))] == [], (
        "the agent was started as the runner's own child: that is the fallback ADR 0146 forbids"
    )
    failed = [e for e in client.events if e["type"] == "run.failed"]
    assert failed and failed[0]["payload"]["phase"] == "isolation"
    assert not [e for e in client.events if e["type"] == "run.started"]


async def test_with_a_launcher_the_agent_and_the_checks_run_through_it(
    stage_input: StageInput, runner_settings: Any, serving: tuple[int, list[Any]]
) -> None:
    port, asked = serving
    settings = _settings(runner_settings, launcher_port=port, launcher_wait_seconds=10)

    outcome, client, _ = await _execute(stage_input, settings)

    assert outcome.exit_code is Exit.OK, outcome.detail
    isolation = [e for e in client.events if e["type"] == "agent.isolation"]
    assert isolation and isolation[0]["payload"]["mode"] == "container"
    commands = [" ".join(map(str, argv)) for argv, _ in asked]
    assert any("fake_agent" in c for c in commands), commands
    assert any("make" in c for c in commands), "the project's checks run in the agent's container"
    assert os.umask(0o022) == lc.SHARED_UMASK, "the runner writes group-writable files into the shared volume"


async def test_the_process_mode_is_unchanged_and_says_so(
    stage_input: StageInput, runner_settings: Any
) -> None:
    outcome, client, runner = await _execute(stage_input, runner_settings)

    assert outcome.exit_code is Exit.OK, outcome.detail
    assert runner.launcher is None
    isolation = [e for e in client.events if e["type"] == "agent.isolation"]
    assert [e["payload"]["mode"] for e in isolation] == ["process"]


def test_the_refusal_has_its_own_exit_code_and_explanation() -> None:
    assert Exit.AGENT_ISOLATION_UNAVAILABLE == 100
    assert explanation(100)
