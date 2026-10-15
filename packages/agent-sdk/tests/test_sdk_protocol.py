"""The SDK agent against the runner's real ACP client (S39-17c).

Each test starts a test agent as a subprocess, exactly as the runner starts a code agent, and drives it
with `choregos_runner.acp.AcpClient`: initialize, session/new with an MCP server, session/prompt.
What is proved is what the runner would see: the guard is asked BEFORE a tool is called, a refusal
stops the action, turns are counted, the result file is valid, standard output stays the protocol's.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from choregos_runner.acp import AcpClient
from choregos_runner.result import load_result

from .conftest import RUN_KEY, Gateway, McpFake, agent_command


def _env(gateway: Gateway, workspace: Path, **extra: str) -> dict[str, str]:
    return {
        "OPENAI_BASE_URL": gateway.url,
        "OPENAI_API_KEY": RUN_KEY,
        "OPENAI_MODEL": "platform/standard",
        "CHOREGOS_MAX_TURNS": "10",
        **extra,
    }


def _servers(mcp: McpFake) -> list[dict[str, Any]]:
    return [{"name": "choregos-tools", "type": "http", "url": mcp.url, "headers": []}]


async def _drive(
    name: str,
    workspace: Path,
    env: dict[str, str],
    *,
    servers: list[dict[str, Any]] | None = None,
    allow: Any = None,
    events: list[str] | None = None,
    prompt: str = "triage this ticket",
) -> tuple[Any, AcpClient]:
    async def on_permission(params: dict[str, Any]) -> tuple[bool, str]:
        call = params["toolCall"]
        if events is not None:
            events.append(f"guard:{call.get('kind')}:{call.get('title')}")
        if allow is not None and not allow(call):
            return False, "refused by the test"
        return True, "ok"

    client = AcpClient(agent_command(name), cwd=str(workspace), env=env, on_permission=on_permission)
    async with client:
        info = await client.initialize()
        await client.new_session(str(workspace), servers or [])
        outcome = await client.prompt(prompt, timeout=60)
    assert outcome is not None
    return info, client


async def test_a_code_agent_runs_through_the_runners_client(
    tmp_path: Path, gateway: Gateway, mcp_server: McpFake
) -> None:
    info, client = await _drive(
        "calls_tool",
        tmp_path,
        _env(gateway, tmp_path),
        servers=_servers(mcp_server),
        events=mcp_server.events,
    )
    assert info["agentInfo"]["name"] == "calls-tool"
    assert info["agentCapabilities"]["loadSession"] is False
    assert client.outcome.stop_reason == "end_turn", client.stderr_tail
    load = load_result(tmp_path / ".choregos" / "result.json")
    assert load.ok, load.error
    assert load.result is not None
    assert load.result.summary == "tool said: hit for refund policy"
    assert load.result.evidence.facts == {"n": 1, "ok": True}
    assert (tmp_path / ".choregos" / "outputs" / "note.md").read_text() == "hit for refund policy / hi"
    # The model was called through the run's gateway with the run's own key.
    assert [g["authorization"] for g in gateway.seen] == [f"Bearer {RUN_KEY}"]
    assert gateway.seen[0]["model"] == "platform/standard"
    # Standard output stayed the protocol's: the agent's print landed on standard error.
    assert any("goes to standard error" in line for line in client.stderr_tail)


async def test_the_guard_is_asked_before_a_tool_is_called(
    tmp_path: Path, gateway: Gateway, mcp_server: McpFake
) -> None:
    await _drive(
        "calls_tool",
        tmp_path,
        _env(gateway, tmp_path),
        servers=_servers(mcp_server),
        events=mcp_server.events,
    )
    events = mcp_server.events
    asked = events.index("guard:other:choregos-tools/knowledge_search")
    called = events.index("mcp:knowledge_search:refund policy")
    assert asked < called, events
    assert "guard:edit:write .choregos/outputs/note.md" in events


async def test_a_refused_tool_call_is_not_made_and_the_step_says_why(
    tmp_path: Path, gateway: Gateway, mcp_server: McpFake
) -> None:
    await _drive(
        "calls_tool",
        tmp_path,
        _env(gateway, tmp_path),
        servers=_servers(mcp_server),
        events=mcp_server.events,
        allow=lambda call: "knowledge_search" not in str(call.get("title")),
    )
    assert not any(e.startswith("mcp:") for e in mcp_server.events), "the guard refused: no call"
    load = load_result(tmp_path / ".choregos" / "result.json")
    assert load.ok and load.result is not None
    assert load.result.status == "failed" and load.result.reason == "permission_denied"
    assert gateway.seen == [], "the agent stopped before the model call"


async def test_a_refused_write_does_not_happen(tmp_path: Path, gateway: Gateway, mcp_server: McpFake) -> None:
    await _drive(
        "calls_tool",
        tmp_path,
        _env(gateway, tmp_path),
        servers=_servers(mcp_server),
        allow=lambda call: call.get("kind") != "edit",
    )
    assert not (tmp_path / ".choregos" / "outputs" / "note.md").exists()
    load = load_result(tmp_path / ".choregos" / "result.json")
    assert load.result is not None and load.result.reason == "permission_denied"


async def test_the_agent_stops_at_the_steps_max_turns(tmp_path: Path, gateway: Gateway) -> None:
    await _drive("two_model_calls", tmp_path, _env(gateway, tmp_path, CHOREGOS_MAX_TURNS="1"))
    assert len(gateway.seen) == 1, "the second call is refused before it is made"
    load = load_result(tmp_path / ".choregos" / "result.json")
    assert load.result is not None
    assert (load.result.status, load.result.reason) == ("failed", "max_turns")


async def test_an_exception_becomes_a_valid_failed_result(tmp_path: Path, gateway: Gateway) -> None:
    await _drive("raises", tmp_path, _env(gateway, tmp_path))
    load = load_result(tmp_path / ".choregos" / "result.json")
    assert load.ok and load.result is not None
    assert (load.result.status, load.result.reason) == ("failed", "agent_error")
    assert "RuntimeError: boom" in load.result.summary


async def test_a_result_the_contract_refuses_is_replaced_by_a_failed_one(
    tmp_path: Path, gateway: Gateway
) -> None:
    await _drive("raises", tmp_path, _env(gateway, tmp_path), prompt="bad-fact")
    load = load_result(tmp_path / ".choregos" / "result.json")
    assert load.ok and load.result is not None and load.result.reason == "agent_error"


async def test_cancelling_the_session_stops_the_function(tmp_path: Path, gateway: Gateway) -> None:
    client = AcpClient(agent_command("slow"), cwd=str(tmp_path), env=_env(gateway, tmp_path))
    async with client:
        await client.initialize()
        await client.new_session(str(tmp_path), [])
        turn = asyncio.create_task(client.prompt("go", timeout=30))
        await asyncio.sleep(1.5)
        await client.cancel()
        outcome = await asyncio.wait_for(turn, timeout=15)
    assert outcome.stop_reason == "cancelled"
    assert not (tmp_path / ".choregos" / "result.json").exists(), "a cancelled step writes no result"


async def test_a_second_prompt_runs_the_function_again(tmp_path: Path, gateway: Gateway) -> None:
    client = AcpClient(agent_command("two_model_calls"), cwd=str(tmp_path), env=_env(gateway, tmp_path))
    async with client:
        await client.initialize()
        await client.new_session(str(tmp_path), [])
        await client.prompt("first", timeout=30)
        await client.prompt("repair: rewrite your result", timeout=30)
    assert len(gateway.seen) == 4
    assert json.loads((tmp_path / ".choregos" / "result.json").read_text())["status"] == "done"
