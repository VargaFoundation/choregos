"""A step carried by a remote agent over A2A (ADR 0062), against the scriptable fake.

The first test is the one that matters: without the `remote` branch the runner looks for an agent
backend called `a2a`, finds none, and the step never reaches the remote agent. The others are the
refusals: a card that drifted (nothing is sent), a task that fails or asks a question, a credential
the remote refuses, a task that never ends — and the access token, which belongs to the endpoint only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from choregos_adapters.a2a import parse_card
from choregos_adapters.fakes.a2a import FakeA2aAgent
from choregos_contracts import AgentRef, DeclaredCost, ModelRef, RemoteCall, StageInput, StageStatus
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient

pytestmark = pytest.mark.asyncio

#: Built at run time: a literal that looks like a token would trip the secrets scan of the CI.
TOKEN = "-".join(["access", "0f3a9c1d" * 3])


def _remote(
    stage_input: StageInput,
    fake: FakeA2aAgent,
    *,
    timeout_s: int = 30,
    digest: str | None = None,
    **transition: Any,
) -> StageInput:
    card = parse_card(fake.card_document())
    return stage_input.model_copy(
        update={
            "repo": None,
            "agent": AgentRef(backend="a2a"),
            "model": ModelRef(litellm_model="none", base_url=""),
            "gateway_key": None,
            "remote": RemoteCall(
                endpoint=fake.endpoint,
                card_url=fake.card_url,
                protocol_version=card.protocol_version,
                skill="work-in-jira",
                card_digest=digest or card.digest,
                timeout_s=timeout_s,
                declared_cost=DeclaredCost(per_task_usd=0.05),
                agent_slug="rovo",
            ),
            "transition": stage_input.transition.model_copy(
                update={"role": "ask-rovo", "outputs": [], **transition}
            ),
        }
    )


async def _run(
    stage_input: StageInput, fake: FakeA2aAgent, settings: Any, **settings_update: Any
) -> tuple[Any, FakeInternalClient]:
    settings = settings.model_copy(
        update={"remote_token": TOKEN, "remote_poll_seconds": 0.01, **settings_update}
    )
    client = FakeInternalClient(stage_input)
    runner = Runner(settings, client)  # type: ignore[arg-type]
    runner.remote_transport = fake.transport()
    outcome = await runner.execute(stage_input, client)  # type: ignore[arg-type]
    return outcome, client


async def test_a_remote_agent_carries_the_step_and_its_answer_goes_through_the_publication(
    stage_input: StageInput, runner_settings: Any
) -> None:
    fake = FakeA2aAgent(
        answer="Three open issues: A-1, A-2, A-3.",
        artifacts=[("issues.csv", "key\nA-1\nA-2\nA-3\n")],
        data_artifacts=[("facts", {"issues_found": 3, "nested": {"x": 1}})],
        timeline=["submitted", "working", "completed"],
    )
    step = _remote(stage_input, fake)
    outcome, client = await _run(step, fake, runner_settings)

    assert outcome.exit_code is Exit.OK, outcome.detail
    result = client.results[-1]
    assert result.status is StageStatus.DONE, result.summary
    assert result.summary == "Three open issues: A-1, A-2, A-3."
    assert "response.md" in client.outputs and client.outputs["response.md"][1].startswith(b"Three open")
    assert "issues.csv" in client.outputs, "the remote agent's artifact is a deliverable of the step"
    # Facts the remote agent states are DECLARED: kept, never listed as measured.
    assert result.evidence.facts == {"issues_found": 3}
    assert "facts.issues_found" not in (result.evidence.measured or [])
    reports = result.artifacts.reports
    assert reports["remote_provenance"] == "remote:rovo"
    assert reports["remote_endpoint"] == fake.endpoint
    assert reports["remote_card_digest"] == step.remote.card_digest  # type: ignore[union-attr]
    assert reports["remote_task_id"]
    types = [event["type"] for event in client.events]
    assert types.index("remote.started") < types.index("remote.update") < types.index("remote.finished")
    sent = fake.requests[0]["params"]["message"]
    assert sent["metadata"]["choregos"]["run_id"] == step.run_id
    assert fake.tokens and set(fake.tokens) == {TOKEN}, "the run's access token is what the endpoint receives"


async def test_the_access_token_is_in_no_journal_no_result_no_summary(
    stage_input: StageInput, runner_settings: Any
) -> None:
    fake = FakeA2aAgent(answer=f"echo {TOKEN}", timeline=["completed"], required_token=TOKEN)
    step = _remote(stage_input, fake)
    _, client = await _run(step, fake, runner_settings)
    dump = json.dumps(list(client.events), default=str) + client.results[-1].model_dump_json()
    assert TOKEN not in dump
    assert TOKEN.encode() not in client.outputs["response.md"][1], "nor in the stored answer"


async def test_a_card_that_changed_stops_the_step_before_any_message(
    stage_input: StageInput, runner_settings: Any
) -> None:
    fake = FakeA2aAgent()
    step = _remote(stage_input, fake, digest="sha256:" + "0" * 64)  # what the version pinned
    _, client = await _run(step, fake, runner_settings)

    result = client.results[-1]
    assert result.status is StageStatus.FAILED and result.reason == "remote_card_changed"
    assert fake.requests == [], "nothing was sent to an agent that is not the one that was reviewed"
    assert fake.tokens == [], "and the token never left the pod"
    assert any(e["type"] == "remote.card_changed" for e in client.events)


async def test_a_failed_task_fails_the_step_with_the_agents_words(
    stage_input: StageInput, runner_settings: Any
) -> None:
    fake = FakeA2aAgent(timeline=["working", "failed"], answer="project KEY does not exist")
    _, client = await _run(_remote(stage_input, fake), fake, runner_settings)
    result = client.results[-1]
    assert result.status is StageStatus.FAILED and result.reason == "remote_failed"
    assert "project KEY does not exist" in result.summary


async def test_input_required_fails_the_step_and_shows_the_question(
    stage_input: StageInput, runner_settings: Any
) -> None:
    fake = FakeA2aAgent(timeline=["working", "input-required"], answer="Which project do you mean?")
    _, client = await _run(_remote(stage_input, fake), fake, runner_settings)
    result = client.results[-1]
    assert result.status is StageStatus.FAILED and result.reason == "remote_input_required"
    assert [q.text for q in result.questions] == ["Which project do you mean?"]


async def test_a_credential_the_remote_refuses_is_auth_required(
    stage_input: StageInput, runner_settings: Any
) -> None:
    fake = FakeA2aAgent(required_token="another")
    _, client = await _run(_remote(stage_input, fake), fake, runner_settings)
    result = client.results[-1]
    assert result.status is StageStatus.FAILED and result.reason == "remote_auth_required"
    assert "reconnect" in result.summary


async def test_an_agent_that_never_ends_is_cancelled_at_the_steps_time(
    stage_input: StageInput, runner_settings: Any
) -> None:
    fake = FakeA2aAgent(timeline=["working"], streaming=False)
    _, client = await _run(_remote(stage_input, fake, timeout_s=1), fake, runner_settings)
    result = client.results[-1]
    assert result.status is StageStatus.FAILED and result.reason == "remote_timeout"
    assert len(fake.cancelled) == 1, "the remote task is cancelled, not abandoned"
    assert result.diagnostics.agent_exit == "timeout"


async def test_an_agent_that_cannot_stream_is_polled(stage_input: StageInput, runner_settings: Any) -> None:
    fake = FakeA2aAgent(streaming=False, timeline=["working", "working", "completed"], answer="polled")
    _, client = await _run(_remote(stage_input, fake), fake, runner_settings)
    assert client.results[-1].status is StageStatus.DONE
    methods = [r["method"] for r in fake.requests]
    assert methods[0] == "message/send" and methods.count("tasks/get") == 2


async def test_a_card_that_says_streaming_but_does_not_is_polled_too(
    stage_input: StageInput, runner_settings: Any
) -> None:
    fake = FakeA2aAgent(method_errors={"stream": -32601}, timeline=["working", "completed"])
    _, client = await _run(_remote(stage_input, fake), fake, runner_settings)
    assert client.results[-1].status is StageStatus.DONE
    assert [r["method"] for r in fake.requests][:2] == ["message/stream", "message/send"]


async def test_the_single_inline_output_takes_the_answer(
    stage_input: StageInput, runner_settings: Any
) -> None:
    fake = FakeA2aAgent(answer="the verdict", timeline=["completed"])
    step = _remote(stage_input, fake, outputs=["rovo_answer"])
    _, client = await _run(step, fake, runner_settings)
    result = client.results[-1]
    assert result.status is StageStatus.DONE
    assert result.outputs.model_dump()["rovo_answer"] == "the verdict"


async def test_a_declared_output_nothing_provides_fails_the_step(
    stage_input: StageInput, runner_settings: Any
) -> None:
    fake = FakeA2aAgent(answer="text", timeline=["completed"])
    step = _remote(stage_input, fake, outputs=["a", "b"])
    _, client = await _run(step, fake, runner_settings)
    result = client.results[-1]
    assert result.status is StageStatus.DONE  # the runner reports; the transition's gate refuses
    assert "a" not in result.outputs.model_dump(exclude_none=True)


async def test_an_artifact_cannot_write_outside_the_outputs_folder(
    stage_input: StageInput, runner_settings: Any, tmp_path: Path
) -> None:
    fake = FakeA2aAgent(artifacts=[("../../escape.txt", "x")], timeline=["completed"])
    _, client = await _run(_remote(stage_input, fake), fake, runner_settings)
    assert not (tmp_path / "escape.txt").exists()
    assert "escape.txt" in client.outputs
