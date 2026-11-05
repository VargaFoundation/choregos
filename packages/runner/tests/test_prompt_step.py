"""A step carried by a prompt agent (ADR 0080): the runner's tool loop, against a scripted model.

Without the `prompt` branch the runner asks the `prompt` backend for a launch plan, which it refuses:
the step never reaches the model. The other tests are the limits the loop keeps: `max_turns` model
calls, the tools it was offered and no other, the run's guard rails on every MCP call, the workspace
it may read, the gateway key that goes to the gateway only, and a result the contract accepts.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from choregos_adapters.fakes import FakeModelServer
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_contracts import (
    AgentRef,
    McpServerRef,
    ModelRef,
    PromptCall,
    StageInput,
    StageResult,
    StageStatus,
    ToolsRef,
)
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient

pytestmark = pytest.mark.asyncio

#: Built at run time: a literal that looks like a key would trip the secrets scan of the CI.
KEY = "-".join(["sk", "run", "7c1e" * 4])
CRM = "http://crm.internal/mcp"


def _prompt(stage_input: StageInput, *, outputs: list[str] | None = None, **update: Any) -> StageInput:
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
            "transition": stage_input.transition.model_copy(
                update={"role": "triage", "outputs": outputs or []}
            ),
            **update,
        }
    )


async def _run(
    stage_input: StageInput, model: FakeModelServer, settings: Any, mcp: FakeMcpServer | None = None
) -> tuple[Any, FakeInternalClient]:
    client = FakeInternalClient(stage_input)
    runner = Runner(settings, client)  # type: ignore[arg-type]
    runner.model_transport = model.transport()
    runner.mcp_transport = (mcp or FakeMcpServer()).transport()
    outcome = await runner.execute(stage_input, client)  # type: ignore[arg-type]
    return outcome, client


def _events(client: FakeInternalClient, type_: str) -> list[dict[str, Any]]:
    return [event["payload"] for event in client.events if event["type"] == type_]


async def test_a_prompt_agent_carries_the_step_and_submits_a_result_the_contract_accepts(
    stage_input: StageInput, runner_settings: Any
) -> None:
    model = FakeModelServer(key=KEY).call(
        "submit_result", {"status": "done", "summary": "Sized M, low risk.", "outputs": {"size": "M"}}
    )
    outcome, client = await _run(_prompt(stage_input, outputs=["size"]), model, runner_settings)

    assert outcome.exit_code is Exit.OK
    result = client.results[-1]
    StageResult.model_validate(result.model_dump(mode="json", by_alias=True))
    assert result.status is StageStatus.DONE
    assert result.outputs.model_dump(exclude_none=True)["size"] == "M"
    assert result.diagnostics.turns == 1 and result.diagnostics.agent_exit == "normal"
    _, body = model.requests[0]
    assert body["model"] == "platform/standard"
    assert {tool["function"]["name"] for tool in body["tools"]} >= {
        "read_file",
        "list_files",
        "submit_result",
    }
    assert "Corriger le total" in body["messages"][1]["content"], "the task reaches the model"


async def test_the_gateway_key_goes_to_the_gateway_only(
    stage_input: StageInput, runner_settings: Any
) -> None:
    crm = FakeMcpServer(
        tools=[{"name": "lookup", "description": "Look up.", "inputSchema": {"type": "object"}}]
    )
    model = FakeModelServer(key=KEY).call("crm__lookup", {"customer": "acme"}).say("Acme is a customer.")
    stage = _prompt(stage_input, tools=ToolsRef(mcp={"crm": McpServerRef(url=CRM)}))
    _, client = await _run(stage, model, runner_settings, crm)

    assert crm.calls == [("lookup", {"customer": "acme"})]
    assert all(headers.get("authorization") == f"Bearer {KEY}" for headers, _ in model.requests)
    assert not any(KEY in json.dumps(headers) or KEY in json.dumps(body) for _, headers, body in crm.received)
    assert KEY not in json.dumps(client.events), "the key is in no journal event"
    assert client.results[-1].status is StageStatus.DONE, "text answers a step that declares no output"


async def test_the_loop_stops_at_max_turns(stage_input: StageInput, runner_settings: Any) -> None:
    model = FakeModelServer(key=KEY)
    for _ in range(10):
        model.call("list_files", {})
    outcome, client = await _run(_prompt(stage_input), model, runner_settings)

    assert len(model.requests) == 4, "no model call past max_turns"
    result = client.results[-1]
    assert (result.status, result.reason) == (StageStatus.FAILED, "max_turns")
    assert result.diagnostics.agent_exit == "limit"
    assert outcome.exit_code is Exit.OK


async def test_a_tool_the_model_was_not_offered_is_never_relayed(
    stage_input: StageInput, runner_settings: Any
) -> None:
    crm = FakeMcpServer(tools=[{"name": "lookup", "description": "", "inputSchema": {"type": "object"}}])
    crm.gestures["drop_table"] = lambda arguments: {"dropped": True}
    model = FakeModelServer(key=KEY).call("crm__drop_table", {}).call("drop_table", {}).say("Nothing to do.")
    stage = _prompt(stage_input, tools=ToolsRef(mcp={"crm": McpServerRef(url=CRM)}))
    _, client = await _run(stage, model, runner_settings, crm)

    assert crm.calls == []
    assert [event["tool"] for event in _events(client, "prompt.tool_refused")] == [
        "crm__drop_table",
        "drop_table",
    ]
    assert "no tool named `crm__drop_table`" in model.requests[1][1]["messages"][-1]["content"]


async def test_an_mcp_call_the_guard_rails_refuse_is_not_relayed(
    stage_input: StageInput, runner_settings: Any
) -> None:
    crm = FakeMcpServer(tools=[{"name": "run", "description": "", "inputSchema": {"type": "object"}}])
    model = FakeModelServer(key=KEY).call("crm__run", {"command": "kubectl delete ns prod"}).say("Done.")
    stage = _prompt(stage_input, tools=ToolsRef(mcp={"crm": McpServerRef(url=CRM)}))
    _, client = await _run(stage, model, runner_settings, crm)

    assert crm.calls == []
    assert "refused by the run's guard rails" in model.requests[1][1]["messages"][-1]["content"]
    assert client.results[-1].diagnostics.permission_denials == 1


async def test_the_workspace_is_read_and_nothing_outside_it(
    stage_input: StageInput, runner_settings: Any
) -> None:
    model = (
        FakeModelServer(key=KEY)
        .call("read_file", {"path": ".choregos/task.md"})
        .call("read_file", {"path": "../../../etc/passwd"})
        .say("Read.")
    )
    await _run(_prompt(stage_input), model, runner_settings)

    assert "Corriger le total" in model.requests[1][1]["messages"][-1]["content"]
    assert "outside the workspace" in model.requests[2][1]["messages"][-1]["content"]


async def test_a_refused_result_goes_back_to_the_model(stage_input: StageInput, runner_settings: Any) -> None:
    model = (
        FakeModelServer(key=KEY)
        .call("submit_result", {"status": "done", "summary": "Sized."})
        .call("submit_result", {"status": "done", "summary": "Sized.", "outputs": {"size": "S"}})
    )
    _, client = await _run(_prompt(stage_input, outputs=["size"]), model, runner_settings)

    assert "`outputs` lacks `size`" in model.requests[1][1]["messages"][-1]["content"]
    assert client.results[-1].status is StageStatus.DONE and client.results[-1].diagnostics.turns == 2


async def test_a_json_answer_is_the_result_under_json_object(
    stage_input: StageInput, runner_settings: Any
) -> None:
    answer = {"status": "done", "summary": "Sized.", "outputs": {"size": "L"}}
    model = FakeModelServer(key=KEY).say(json.dumps(answer))
    stage = _prompt(stage_input, outputs=["size"], prompt=PromptCall(response_format="json_object"))
    _, client = await _run(stage, model, runner_settings)

    assert model.requests[0][1]["response_format"] == {"type": "json_object"}
    assert client.results[-1].outputs.model_dump(exclude_none=True)["size"] == "L"


async def test_a_spent_key_stops_the_step_on_budget(stage_input: StageInput, runner_settings: Any) -> None:
    refusal = {"error": {"message": "Budget has been exceeded! Current cost: 5.1", "type": "budget_exceeded"}}
    model = FakeModelServer(key=KEY).refuse(400, refusal)
    outcome, client = await _run(_prompt(stage_input), model, runner_settings)

    assert outcome.exit_code is Exit.BUDGET_EXCEEDED
    assert client.results[-1].reason == "budget"


async def test_a_declared_file_is_written_where_the_runner_stores_it(
    stage_input: StageInput, runner_settings: Any
) -> None:
    transition = stage_input.transition.model_copy(
        update={"role": "write", "outputs": ["notes"], "output_types": {"notes": "csv"}}
    )
    model = (
        FakeModelServer(key=KEY)
        .call("write_output", {"name": "../escape.csv", "content": "x"})
        .call("write_output", {"name": "notes.csv", "content": "key\nA-1\n"})
        .call("submit_result", {"status": "done", "summary": "Written."})
    )
    _, client = await _run(_prompt(stage_input, transition=transition), model, runner_settings)

    assert "is not a file this step declares" in model.requests[1][1]["messages"][-1]["content"]
    assert client.results[-1].status is StageStatus.DONE
    assert "notes" in client.results[-1].artifacts.files


async def test_an_mcp_server_on_a_denied_host_is_never_reached(
    stage_input: StageInput, runner_settings: Any
) -> None:
    crm = FakeMcpServer(tools=[{"name": "lookup", "description": "", "inputSchema": {"type": "object"}}])
    model = FakeModelServer(key=KEY).call("crm__lookup", {}).say("Nothing.")
    permissions = stage_input.permissions.model_copy(update={"deny_domains": ["crm.internal"]})
    stage = _prompt(stage_input, tools=ToolsRef(mcp={"crm": McpServerRef(url=CRM)}), permissions=permissions)
    _, client = await _run(stage, model, runner_settings, crm)

    assert crm.received == [], "the denied host sees no request, not even the listing"
    assert "egress refused" in _events(client, "prompt.mcp_unavailable")[0]["error"]
    assert "crm__lookup" not in {tool["function"]["name"] for tool in model.requests[0][1]["tools"]}


async def test_the_last_notice_of_an_error_answer_reaches_the_model(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """The platform's tool server ends a relayed answer with the calls left under the cap, within the
    loop's own bound (S57-04); the loop's error prefix must not push that line out of the cut, and the
    loop reads as much as that server lets through: past the platform's bound on a result (#1374)."""
    from choregos_adapters.errors import AdapterError
    from choregos_core.mcp import RESULT_MAX_BYTES
    from choregos_tools_mcp.answer import MAX_TEXT, render_answer

    answer = render_answer({"status_code": 404, "result": {"error": "x" * 100_000}, "remaining": 2})
    rendered = answer["content"][0]["text"]
    assert answer["isError"] and rendered.endswith("Tool calls left in this run: 2.")
    assert RESULT_MAX_BYTES < len(rendered) <= MAX_TEXT, "the tool server's own cut, past the bound"

    def refuse(arguments: dict[str, Any]) -> dict[str, Any]:
        raise AdapterError(rendered)

    crm = FakeMcpServer(tools=[{"name": "lookup", "description": "", "inputSchema": {"type": "object"}}])
    crm.gestures["lookup"] = refuse
    model = FakeModelServer(key=KEY).call("crm__lookup", {}).say("Nothing found.")
    stage = _prompt(stage_input, tools=ToolsRef(mcp={"crm": McpServerRef(url=CRM)}))
    await _run(stage, model, runner_settings, crm)

    read = model.requests[1][1]["messages"][-1]["content"]
    assert read.startswith("the tool reported an error: ")
    assert read.endswith("Tool calls left in this run: 2."), read[-80:]
    assert read == "the tool reported an error: " + rendered, "the loop cuts nothing the server let through"


async def test_an_answer_read_up_to_a_raised_bound_reaches_the_model_whole(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """#1383: the tool server reads a result up to the bound the API names (an operation's cap, up to
    the deployment's ceiling); the loop lets through as much, the last notice included."""
    from choregos_core.mcp import RESULT_CEILING_BYTES
    from choregos_tools_mcp.answer import MAX_NOTICES, render_answer

    page = {"objects": ["o" * 200] * 1_000, "next_cursor": "after"}  # 200 KB: a 256 KiB page
    rendered = render_answer(
        {"status_code": 200, "result": page, "remaining": 2, "result_max_bytes": 262_144}
    )["content"][0]["text"]
    assert len(rendered) > 200_000 and "[cut by the tool server" not in rendered

    crm = FakeMcpServer(tools=[{"name": "lookup", "description": "", "inputSchema": {"type": "object"}}])
    crm.answers["lookup"] = rendered
    model = FakeModelServer(key=KEY).call("crm__lookup", {}).say("Read.")
    stage = _prompt(stage_input, tools=ToolsRef(mcp={"crm": McpServerRef(url=CRM)}))
    await _run(stage, model, runner_settings, crm)

    assert model.requests[1][1]["messages"][-1]["content"] == rendered, "whole, the calls left included"
    from choregos_runner import prompt

    assert prompt._MAX_TOOL_TEXT == RESULT_CEILING_BYTES + MAX_NOTICES, "the most the tool server writes"
