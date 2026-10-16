"""The A2A client against the scriptable fake (ADR 0062): 0.3 and 1.0 spellings, streaming, polling."""

from __future__ import annotations

import pytest
from choregos_adapters.a2a import (
    A2aAuthRequired,
    A2aClient,
    A2aError,
    A2aMethodNotSupported,
    text_part,
)
from choregos_adapters.fakes.a2a import FakeA2aAgent


def _client(fake: FakeA2aAgent, token: str | None = "tok", version: str | None = None) -> A2aClient:
    return A2aClient(fake.endpoint, token, version or fake.protocol_version, transport=fake.transport())


async def test_send_returns_the_task_and_get_task_advances_it() -> None:
    fake = FakeA2aAgent(
        timeline=["working", "completed"], answer="3 issues", artifacts=[("report.md", "# r")]
    )
    async with _client(fake) as client:
        task = await client.send(client.message([text_part("hi")]))
        assert task.state == "working" and not task.terminal
        done = await client.get_task(task.id)
    assert done.state == "completed" and done.terminal
    assert done.message == "3 issues"
    assert [(a.name, a.text()) for a in done.artifacts] == [("report.md", "# r")]
    assert [r["method"] for r in fake.requests] == ["message/send", "tasks/get"]


async def test_stream_yields_status_artifact_and_final_events() -> None:
    fake = FakeA2aAgent(
        timeline=["submitted", "working", "completed"], answer="ok", artifacts=[("a.txt", "x")]
    )
    async with _client(fake) as client:
        events = [e async for e in client.stream(client.message([text_part("go")]))]
    kinds = [(e.kind, e.state) for e in events]
    assert kinds == [
        ("status", "submitted"),
        ("status", "working"),
        ("artifact", None),
        ("status", "completed"),
    ]
    assert events[-1].final and events[-1].message == "ok"
    assert events[2].artifact is not None and events[2].artifact.text() == "x"
    assert fake.requests[0]["method"] == "message/stream"


async def test_a_1_0_agent_is_spoken_to_in_1_0_names() -> None:
    fake = FakeA2aAgent(protocol_version="1.0.0")
    async with _client(fake) as client:
        task = await client.send(client.message([text_part("x")]))
        await client.get_task(task.id)
        await client.cancel(task.id)
        _ = [e async for e in client.stream(client.message([text_part("x")]))]
    assert [r["method"] for r in fake.requests] == [
        "SendMessage",
        "GetTask",
        "CancelTask",
        "SendStreamingMessage",
    ]


async def test_a_0_3_agent_never_hears_a_1_0_name() -> None:
    fake = FakeA2aAgent(protocol_version="0.3.0")
    async with _client(fake) as client:
        await client.send(client.message([text_part("x")]))
    assert {r["method"] for r in fake.requests} == {"message/send"}


async def test_cancel_reaches_the_remote() -> None:
    fake = FakeA2aAgent()
    async with _client(fake) as client:
        task = await client.send(client.message([text_part("x")]))
        cancelled = await client.cancel(task.id)
    assert cancelled.state == "canceled" and fake.cancelled == [task.id]


async def test_a_401_is_auth_required_not_a_generic_error() -> None:
    fake = FakeA2aAgent(required_token="right")
    async with _client(fake, token="wrong") as client:
        with pytest.raises(A2aAuthRequired):
            await client.send(client.message([text_part("x")]))
        with pytest.raises(A2aAuthRequired):
            _ = [e async for e in client.stream(client.message([text_part("x")]))]
    async with _client(fake, token="right") as client:
        assert (await client.send(client.message([text_part("x")]))).state == "working"


async def test_an_unsupported_method_is_told_apart() -> None:
    fake = FakeA2aAgent(method_errors={"stream": -32601})
    async with _client(fake) as client:
        with pytest.raises(A2aMethodNotSupported):
            _ = [e async for e in client.stream(client.message([text_part("x")]))]


async def test_a_json_rpc_error_is_an_error_with_its_code() -> None:
    fake = FakeA2aAgent(method_errors={"send": -32000})
    async with _client(fake) as client:
        with pytest.raises(A2aError) as caught:
            await client.send(client.message([text_part("x")]))
    assert caught.value.code == -32000


async def test_the_token_is_sent_as_bearer_and_only_there() -> None:
    fake = FakeA2aAgent()
    async with _client(fake, token="s3cret-value") as client:
        await client.send(client.message([text_part("x")], metadata={"choregos": {"run_id": "r1"}}))
    assert fake.tokens == ["s3cret-value"]
    assert "s3cret-value" not in repr(fake.requests)
    assert fake.requests[0]["params"]["message"]["metadata"] == {"choregos": {"run_id": "r1"}}


async def test_states_are_normalised_across_versions() -> None:
    from choregos_adapters.a2a.client import normalise_state

    assert normalise_state("TASK_STATE_COMPLETED") == "completed"
    assert normalise_state("input_required") == "input-required"
    assert normalise_state("cancelled") == "canceled"
