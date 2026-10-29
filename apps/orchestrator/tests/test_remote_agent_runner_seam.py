"""From the orchestrator's spec to the runner's call: the seam of a remote agent's step (ADR 0062).

`prepare_stage` and `start_run` produce what the executor puts in the pod: a StageInput and, in the
run's Secret, the access token. The runner is started here with exactly that — the spec's input and
`CHOREGOS_RUN_REMOTE_TOKEN` — against the scriptable A2A agent. What is proved: the remote agent
receives the token the orchestrator minted and the step the orchestrator prepared, and the result
the runner posts is an ordinary `StageResult` (the transition's gates read it like any other).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from choregos_adapters.a2a import parse_card
from choregos_adapters.fakes.a2a import FakeA2aAgent
from choregos_contracts import ContextPack, StageResult, StageStatus
from choregos_orchestrator.activities import stage as stage_activities
from choregos_runner.config import RunnerSettings
from choregos_runner.runner import Runner

from .conftest import Fixture
from .test_remote_agent_step import HOST, TOKEN, _decor, _plan, _runtime

pytestmark = pytest.mark.asyncio


class InternalApi:
    """What the runner needs of the internal API, in memory."""

    def __init__(self) -> None:
        self.results: list[StageResult] = []
        self.outputs: dict[str, bytes] = {}
        self.events: list[dict[str, Any]] = []

    async def fetch_context(self) -> ContextPack:
        return ContextPack.empty("t")

    async def fetch_inputs(self) -> list[dict[str, Any]]:
        return []

    async def post_events(self, events: list[dict[str, Any]]) -> int:
        self.events.extend(events)
        return len(events)

    async def post_result(self, result: StageResult) -> dict[str, Any]:
        self.results.append(result)
        return {"status": "recorded"}

    async def put_output(self, name: str, type_: str, data: bytes, digest: str) -> dict[str, Any]:
        self.outputs[name] = data
        return {"name": name}

    async def fetch_git_token(self) -> str | None:
        return None

    async def aclose(self) -> None:
        return None


async def _pod(setup: Fixture, fake: FakeA2aAgent, tmp_path: Path) -> tuple[Any, InternalApi]:
    """Prepares and starts the step, then runs the runner with what the executor would give the pod."""
    prepared = await stage_activities.prepare_stage(_plan(setup))
    await stage_activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )
    spec = setup.adapters.executor.started[-1]
    api = InternalApi()
    settings = RunnerSettings(
        run_id=spec.run_id, workspace=tmp_path / "workspace", run_token="run-token-value"
    ).model_copy(
        # What the executor puts in the pod's environment: `CHOREGOS_RUN_REMOTE_TOKEN`.
        update={"remote_token": spec.remote_token or "", "remote_poll_seconds": 0.01}
    )
    runner = Runner(settings, api)  # type: ignore[arg-type]
    runner.remote_transport = fake.transport()
    stage_input = spec.stage_input
    assert stage_input is not None and stage_input.remote is not None
    # The pinned card is the one the agent had when the version was published: the fake's.
    return await runner.execute(stage_input, api), api  # type: ignore[arg-type]


async def test_the_runner_calls_the_remote_agent_with_the_token_the_orchestrator_minted(
    setup: Fixture, tmp_path: Path
) -> None:
    await _decor(setup)
    fake = FakeA2aAgent(
        host=f"https://{HOST}",
        required_token=TOKEN,
        answer="Three open issues.",
        artifacts=[("issues.csv", "k\n1\n")],
    )
    fake.security = "oauth2"
    outcome, api = await _pod(setup, fake, tmp_path)

    assert outcome.exit_code == 0, outcome.detail
    result = api.results[-1]
    assert result.status is StageStatus.DONE and result.summary == "Three open issues."
    assert set(fake.tokens) == {TOKEN}, "the remote agent saw the token the orchestrator minted, and no other"
    assert api.outputs["response.md"].startswith(b"Three open issues.") and "issues.csv" in api.outputs
    assert result.artifacts.reports["remote_provenance"] == "remote:rovo"


async def test_an_api_key_reaches_the_remote_agent_alone_in_its_header(
    setup: Fixture, tmp_path: Path
) -> None:
    """S48-03 end to end: the header's name rides in the StageInput, the key in the run's Secret, and
    the agent reads the key where its card said, never as a bearer token."""
    fake = FakeA2aAgent(
        host=f"https://{HOST}",
        security="api_key",
        api_key_header="x-sn-apikey",
        required_token=TOKEN,
        answer="Incident triaged.",
    )
    card = parse_card(fake.card_document())
    pinned = {"url": card.url, "protocol_version": card.protocol_version, "digest": card.digest}
    await _decor(setup, runtime=_runtime(pinned_card={**pinned, "skills": fake.skills}), header="x-sn-apikey")
    outcome, api = await _pod(setup, fake, tmp_path)

    assert outcome.exit_code == 0, outcome.detail
    assert api.results[-1].status is StageStatus.DONE, api.results[-1].summary
    assert set(fake.tokens) == {TOKEN}, "the key the organisation sealed, read in `x-sn-apikey`"
    assert not any(r["has_authorization"] for r in fake.requests)
