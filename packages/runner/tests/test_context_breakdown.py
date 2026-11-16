"""The context breakdown of a prompt step and of a hosted container (ADR 0141 §3-5).

A prompt step's every model call is measured from the request the runner sent and the `prompt_tokens`
the model reported: the sections sum to that total, the MCP tools count as `tools.mcp` and the runner's
own as `tools.platform`, and the journal never carries the task's text. The hosted container's calls are
measured by the loopback model from the body it forwards and the usage the gateway's answer reports —
a JSON answer or a stream —, and an answer without usage records nothing.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from choregos_adapters.fakes import FakeModelServer
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_contracts import (
    AgentRef,
    HostedCall,
    McpServerRef,
    ModelRef,
    PromptCall,
    StageInput,
    ToolsRef,
)
from choregos_core.context_breakdown import EVENT, Hints
from choregos_core.hosted import LOOPBACK_KEY
from choregos_runner.loopback.model import ModelProxy
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient

pytestmark = pytest.mark.asyncio

KEY = "-".join(["sk", "run", "3e9d" * 4])
CRM = "http://crm.internal/mcp"
#: Words the person wrote, which must reach the model and no event.
CANARY = "canary-41a7-only-the-model-reads-this"
LOOPBACK = {"authorization": f"Bearer {LOOPBACK_KEY}"}


def _prompt(stage_input: StageInput, **update: Any) -> StageInput:
    no_repo = stage_input.project.model_copy(
        update={"test_command": "", "lint_command": "", "typecheck_command": ""}
    )
    return stage_input.model_copy(
        update={
            "repo": None,
            "project": no_repo,
            "agent": AgentRef(backend="prompt"),
            "model": ModelRef(litellm_model="platform/standard", base_url="http://gateway:4000/v1"),
            "gateway_key": KEY,
            "prompt": PromptCall(),
            "budget": stage_input.budget.model_copy(update={"max_turns": 4}),
            "playbook": stage_input.playbook.model_copy(update={"prompt": f"Triage the ticket. {CANARY}"}),
            "transition": stage_input.transition.model_copy(update={"role": "triage", "outputs": []}),
            **update,
        }
    )


async def test_a_prompt_steps_calls_are_measured_to_the_reported_total(
    stage_input: StageInput, runner_settings: Any
) -> None:
    crm = FakeMcpServer(
        tools=[
            {"name": "lookup", "description": "Look up a customer. " * 40, "inputSchema": {"type": "object"}}
        ]
    )
    model = FakeModelServer(key=KEY).call("crm__lookup", {"customer": "acme"}).say("Acme is a customer.")
    model.usage = {"prompt_tokens": 2345, "completion_tokens": 12}
    stage = _prompt(stage_input, tools=ToolsRef(mcp={"crm": McpServerRef(url=CRM)}))
    client = FakeInternalClient(stage)
    runner = Runner(runner_settings, client)  # type: ignore[arg-type]
    runner.model_transport = model.transport()
    runner.mcp_transport = crm.transport()

    await runner.execute(stage, client)  # type: ignore[arg-type]

    breakdowns = [event["payload"] for event in client.events if event["type"] == EVENT]
    assert [b["call"] for b in breakdowns] == [1, 2], "the first call, and the last"
    for kept in breakdowns:
        assert sum(kept["sections"].values()) == 2345 == kept["prompt_tokens"]
        assert kept["sections"]["tools.mcp"] > 0, "crm__lookup is an MCP tool"
        assert kept["sections"]["tools.platform"] > 0, "read_file, list_files, submit_result are the runner's"
        assert kept["sections"]["system"] > 0, "the runner's system prompt and the playbook"
        assert (kept["model"], kept["source"], kept["max_input_tokens"]) == (
            "platform/standard",
            "loop",
            None,
        )
    assert CANARY in json.dumps(model.requests[0][1]), "the playbook reaches the model"
    assert CANARY not in json.dumps(breakdowns), "and no breakdown"


def _hosted(stage_input: StageInput) -> StageInput:
    return stage_input.model_copy(
        update={
            "agent": AgentRef(backend="hosted"),
            "model": ModelRef(litellm_model="platform/standard", base_url="http://gateway:4000/v1"),
            "gateway_key": KEY,
            "hosted": HostedCall(
                image="registry.example/a@sha256:" + "b" * 64, agent_slug="a", agent_version=1
            ),
            "tools": ToolsRef(mcp={"crm": McpServerRef(url=CRM)}),
        }
    )


async def _call(
    stage_input: StageInput, path: str, body: dict[str, Any], answer: httpx.Response
) -> list[tuple[str, dict[str, Any]]]:
    recorded: list[tuple[str, dict[str, Any]]] = []

    async def record(type_: str, payload: dict[str, Any]) -> None:
        recorded.append((type_, payload))

    stage = _hosted(stage_input)
    hints = Hints(
        parts={"messages": [f"Fix the total. {CANARY}"]}, foreign=True, mcp_servers=frozenset({"crm"})
    )
    proxy = ModelProxy(stage, record, transport=httpx.MockTransport(lambda _: answer), hints=hints)
    container = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy.app()), base_url="http://127.0.0.1:4001"
    )
    async with container:
        sent = await container.post(f"/v1/{path}", headers=LOOPBACK, json=body)
    assert sent.status_code == 200
    await proxy.aclose()
    return [(type_, payload) for type_, payload in recorded if type_ == EVENT]


def _chat_body() -> dict[str, Any]:
    return {
        "model": "gpt-4.1",
        "messages": [
            {"role": "system", "content": "You are the container's agent. " * 30},
            {"role": "user", "content": f"Fix the total. {CANARY}"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {"name": "crm__lookup", "description": "Look up.", "parameters": {}},
            },
            {
                "type": "function",
                "function": {"name": "shell", "description": "Run it. " * 20, "parameters": {}},
            },
        ],
    }


async def test_a_hosted_json_answer_is_measured_and_what_the_platform_did_not_write_is_backend(
    stage_input: StageInput,
) -> None:
    answer = httpx.Response(
        200, json={"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1500}}
    )
    (event,) = await _call(stage_input, "chat/completions", _chat_body(), answer)

    _, kept = event
    assert kept["source"] == "hosted_proxy" and kept["prompt_tokens"] == 1500
    assert sum(kept["sections"].values()) == 1500
    assert kept["sections"]["backend"] > kept["sections"]["messages"] > 0, "its system prompt and `shell`"
    assert kept["sections"]["tools.mcp"] > 0 and kept["sections"]["tools.platform"] == 0
    assert CANARY not in json.dumps(kept)


async def test_a_hosted_stream_is_measured_from_its_final_usage(stage_input: StageInput) -> None:
    chunks = [
        {"choices": [{"delta": {"content": "o"}}]},
        {"choices": [{"delta": {"content": "k"}}]},
        {"choices": [], "usage": {"prompt_tokens": 777, "completion_tokens": 2}},
    ]
    stream = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    answer = httpx.Response(200, content=stream.encode(), headers={"content-type": "text/event-stream"})

    (event,) = await _call(stage_input, "chat/completions", {**_chat_body(), "stream": True}, answer)

    assert event[1]["prompt_tokens"] == 777 and sum(event[1]["sections"].values()) == 777


async def test_an_anthropic_stream_is_measured_from_its_message_start(stage_input: StageInput) -> None:
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 40, "cache_read_input_tokens": 960}},
            },
        ),
        ("message_delta", {"type": "message_delta", "usage": {"output_tokens": 9}}),
    ]
    stream = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
    answer = httpx.Response(200, content=stream.encode(), headers={"content-type": "text/event-stream"})
    body = {"system": "Be the agent.", "messages": [{"role": "user", "content": "hi"}], "stream": True}

    (event,) = await _call(stage_input, "messages", body, answer)

    assert event[1]["prompt_tokens"] == 1000, "the input, cache reads included"


async def test_an_answer_without_usage_records_nothing(stage_input: StageInput) -> None:
    answer = httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    assert await _call(stage_input, "chat/completions", _chat_body(), answer) == []
