"""ACP protocol: encoding, decoding, and coverage of the v0.11 messages."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from tranibor_runner.acp import AcpClient, Notification, Request, Response, decode
from tranibor_runner.acp.protocol import (
    FS_READ_TEXT_FILE,
    INITIALIZE,
    SESSION_NEW,
    SESSION_PROMPT,
    SESSION_REQUEST_PERMISSION,
    SESSION_UPDATE,
    error_response,
)

FAKE_AGENT = Path(__file__).parent / "fixtures" / "fake_agent.py"


def test_request_roundtrip() -> None:
    request = Request(id=1, method=INITIALIZE, params={"protocolVersion": 1})
    decoded = decode(request.encode())
    assert isinstance(decoded, Request)
    assert decoded.method == INITIALIZE and decoded.params["protocolVersion"] == 1


def test_response_and_error_roundtrip() -> None:
    ok = decode(Response(id=2, result={"sessionId": "s1"}).encode())
    assert isinstance(ok, Response) and not ok.failed and ok.result == {"sessionId": "s1"}
    ko = decode(error_response(3, -32601, "inconnu").encode())
    assert isinstance(ko, Response) and ko.failed and ko.error["code"] == -32601


def test_notification_roundtrip() -> None:
    notification = Notification(method=SESSION_UPDATE, params={"sessionId": "s1"})
    decoded = decode(notification.encode())
    assert isinstance(decoded, Notification) and decoded.method == SESSION_UPDATE


@pytest.mark.parametrize("line", ["", "   ", "noise on stdout", "{broken", "[]", "null"])
def test_garbage_lines_are_ignored(line: str) -> None:
    assert decode(line) is None


def test_encoding_is_one_json_per_line() -> None:
    encoded = Request(id=1, method="m", params={"text": "accented ☃"}).encode()
    assert encoded.endswith(b"\n")
    assert encoded.count(b"\n") == 1
    assert json.loads(encoded)["params"]["text"] == "accented ☃"


async def _client(tmp_path: Path, script: dict[str, Any], **kwargs: Any) -> AcpClient:
    return AcpClient(
        [sys.executable, str(FAKE_AGENT)],
        cwd=str(tmp_path),
        env={"FAKE_AGENT_SCRIPT": json.dumps(script), "TRANIBOR_WORKSPACE_PATH": str(tmp_path)},
        **kwargs,
    )


async def test_full_session_against_fake_agent(tmp_path: Path) -> None:
    updates: list[dict[str, Any]] = []
    permissions: list[dict[str, Any]] = []

    async def on_update(params: dict[str, Any]) -> None:
        updates.append(params)

    async def on_permission(params: dict[str, Any]) -> tuple[bool, str]:
        permissions.append(params)
        return True, "allowed by the test"

    client = await _client(
        tmp_path,
        {"turns": [{"messages": ["bonjour"], "writes": [{"path": "a.txt", "content": "x"}]}]},
        on_update=on_update,
        on_permission=on_permission,
    )
    async with client:
        init = await client.initialize()
        assert init["agentInfo"]["name"] == "fake-agent"
        session = await client.new_session(str(tmp_path))
        assert session == "sess-fake-1"
        outcome = await client.prompt("travaille", timeout=30)
    assert outcome.stop_reason == "end_turn"
    assert outcome.turns == 1
    assert updates and permissions
    assert (tmp_path / "a.txt").read_text() == "x"
    assert outcome.tool_calls >= 1


async def test_denied_permission_prevents_the_write(tmp_path: Path) -> None:
    """A refused permission is not bypassed: the file does not exist (conformance §2.2)."""

    async def deny(params: dict[str, Any]) -> tuple[bool, str]:
        return False, "refused by the test"

    client = await _client(
        tmp_path,
        {"turns": [{"writes": [{"path": "interdit.txt", "content": "x"}]}]},
        on_permission=deny,
    )
    async with client:
        await client.initialize()
        await client.new_session(str(tmp_path))
        outcome = await client.prompt("write", timeout=30)
    assert not (tmp_path / "interdit.txt").exists()
    assert outcome.permission_denials == 1


async def test_fs_requests_are_refused(tmp_path: Path) -> None:
    """The runner does not expose the file system: the agent writes itself, under the diff's control."""
    client = await _client(tmp_path, {"turns": [{}]})
    async with client:
        await client.initialize()
        response = await client.request(FS_READ_TEXT_FILE, {"path": "/etc/passwd"}, timeout=10)
    assert response.failed


async def test_unreachable_binary_raises(tmp_path: Path) -> None:
    from tranibor_runner.acp import AgentUnreachableError

    client = AcpClient(["/introuvable/agent"], cwd=str(tmp_path))
    with pytest.raises(AgentUnreachableError):
        await client.start()


async def test_initialize_failure_is_reported(tmp_path: Path) -> None:
    from tranibor_runner.acp import AgentUnreachableError

    client = await _client(tmp_path, {"fail_initialize": True})
    async with client:
        with pytest.raises(AgentUnreachableError):
            await client.initialize()


async def test_prompt_timeout_cancels_session(tmp_path: Path) -> None:
    """A live agent that has not ended its turn when the time runs out: the turn is cancelled
    (`session/cancel`) and ends as a `timeout`, not as an unreachable agent (S65-30).

    The agent holds its turn until it is cancelled, so the timeout always fires first: the test
    used to give a fast agent 1 ms and accept either outcome, which proved nothing locally (the
    agent answered in time) and raised `AgentUnreachableError` on a slow CI runner (#1707)."""
    client = await _client(tmp_path, {"turns": [{"until_cancelled": True}, {"stop_reason": "end_turn"}]})
    async with client:
        await client.initialize()
        session_id = await client.new_session(str(tmp_path))
        outcome = await client.prompt("work", timeout=0.2)
        assert outcome.stop_reason == "timeout"
        assert outcome.cancelled
        # The pipe is ordered: once the next turn is answered, the agent has read the cancel.
        following = await client.prompt("go on", timeout=30)
        assert following.stop_reason == "end_turn"
    cancelled = json.loads((tmp_path / "cancelled.json").read_text(encoding="utf-8"))
    assert cancelled == {"sessionId": session_id}, "the agent was told to stop its turn"


async def test_a_prompt_to_an_agent_that_died_is_unreachable_not_a_timeout(tmp_path: Path) -> None:
    """The other half of the distinction: an agent whose process exited during its turn did not run
    out of time, it crashed — `prompt` raises, saying the exit code, and cancels nothing."""
    from tranibor_runner.acp.client import AgentUnreachableError

    client = await _client(tmp_path, {"turns": [{"exit": 3}]})
    async with client:
        await client.initialize()
        await client.new_session(str(tmp_path))
        with pytest.raises(AgentUnreachableError) as refusal:
            await client.prompt("work", timeout=1.0)
    assert "code 3" in str(refusal.value), refusal.value


async def test_an_agent_that_exits_before_initialize_is_said_to_have_exited_at_once(tmp_path: Path) -> None:
    """#1986: an agent that died at start was seen only when `initialize` timed out, 60 s later. The
    request is raced against the agent's end: it fails when the agent exits, with its code and stderr."""
    from tranibor_runner.acp import AgentExitedError

    client = await _client(tmp_path, {"exit_at_start": 1})
    async with client:
        started = asyncio.get_running_loop().time()
        with pytest.raises(AgentExitedError) as exited:
            await asyncio.wait_for(client.initialize(), timeout=15)
        assert asyncio.get_running_loop().time() - started < 15
    assert exited.value.code == 1
    assert "exited before it answered `initialize` (code 1)" in str(exited.value)
    assert "read-only file system" in str(exited.value), "its last error lines are said"


async def test_through_the_launcher_an_agent_that_exits_at_start_is_said_to_have_exited_at_once(
    tmp_path: Path,
) -> None:
    """The same under `container` (ADR 0146): the launcher reports the remote process's exit code,
    and the request fails on it instead of at its timeout (#1986)."""
    from tranibor_runner import launcher as lc
    from tranibor_runner.acp import AgentExitedError

    from .conftest import free_port

    port = free_port()
    server = await asyncio.start_server(lc.handle, "127.0.0.1", port)
    try:
        client = AcpClient(
            [sys.executable, str(FAKE_AGENT)],
            cwd=str(tmp_path),
            env={"FAKE_AGENT_SCRIPT": json.dumps({"exit_at_start": 7})},
            launcher=lc.Launcher("127.0.0.1", port),
        )
        async with client:
            with pytest.raises(AgentExitedError) as exited:
                await asyncio.wait_for(client.initialize(), timeout=15)
    finally:
        server.close()
        await server.wait_closed()
    assert exited.value.code == 7
    assert "read-only file system" in str(exited.value)


async def test_a_refused_session_is_a_protocol_error_naming_the_agents_error(tmp_path: Path) -> None:
    from tranibor_runner.acp import AgentProtocolError

    client = await _client(tmp_path, {"fail_session_new": True})
    async with client:
        await client.initialize()
        with pytest.raises(AgentProtocolError, match=r"session/new refused.*directory"):
            await client.new_session(str(tmp_path))


def test_protocol_methods_are_covered() -> None:
    """The protocol methods used by the runner are all named explicitly."""
    from tranibor_runner.acp import protocol

    expected = {
        INITIALIZE,
        SESSION_NEW,
        SESSION_PROMPT,
        SESSION_UPDATE,
        SESSION_REQUEST_PERMISSION,
        protocol.SESSION_CANCEL,
        protocol.SESSION_LOAD,
        protocol.AUTHENTICATE,
        protocol.FS_READ_TEXT_FILE,
        protocol.FS_WRITE_TEXT_FILE,
    }
    assert len(expected) == 10


# ──────── a silent agent must say why it is (2026-09-27) ────────


@pytest.mark.asyncio
async def test_an_agent_that_does_not_answer_explains_what_is_known(tmp_path: Path) -> None:
    """A bare `TimeoutError` teaches nothing; this refusal names what to look at.

    On the dev tenant, `opencode` did not answer `initialize` and the pod's journal carried
    ONLY the `asyncio.wait_for` stack: nothing about the command, nothing about the state of
    the process, nothing from its error output, although `stderr_tail` has collected it since
    day one and nobody read it at that place.
    """
    from tranibor_runner.acp.client import AgentUnreachableError

    silent = tmp_path / "silent.py"
    silent.write_text(
        "import sys, time\n"
        "print('I start but I do not speak ACP', file=sys.stderr, flush=True)\n"
        "print('provider not found: @ai-sdk/openai-compatible', file=sys.stderr, flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    client = AcpClient(command=[sys.executable, str(silent)], cwd=tmp_path)
    await client.start()
    try:
        with pytest.raises(AgentUnreachableError) as refusal:
            await client.request(INITIALIZE, {"protocolVersion": 1}, timeout=1.0)
    finally:
        await client.stop()

    message = str(refusal.value)
    assert INITIALIZE in message, message
    assert "silent.py" in message, "the refusal must name the command launched"
    assert "alive" in message, "it must say that the process did not crash"
    assert "provider not found" in message, "it must carry the agent's error output"


@pytest.mark.asyncio
async def test_an_agent_that_dies_says_so_too(tmp_path: Path) -> None:
    """The dead process is the other half of the diagnosis: its exit code."""
    from tranibor_runner.acp.client import AgentUnreachableError

    dead = tmp_path / "dead.py"
    dead.write_text("import sys\nsys.stderr.write('config unreadable\\n')\nsys.exit(3)\n", encoding="utf-8")
    client = AcpClient(command=[sys.executable, str(dead)], cwd=tmp_path)
    await client.start()
    try:
        with pytest.raises(AgentUnreachableError) as refusal:
            await client.request(INITIALIZE, {"protocolVersion": 1}, timeout=1.0)
    finally:
        await client.stop()

    message = str(refusal.value)
    assert "code 3" in message, message
    assert "config unreadable" in message, message


# ──────── an ACP message longer than 64 KiB (#498) ────────

_CHATTY = (
    "import json, os, sys\n"
    "size = int(os.environ['SIZE'])\n"
    "for line in sys.stdin:\n"
    "    message = json.loads(line)\n"
    "    if 'id' not in message:\n"
    "        continue\n"
    "    update = {'sessionUpdate': 'tool_call_update', 'status': 'completed',\n"
    "              'rawOutput': {'output': 'x' * size}}\n"
    "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'method': 'session/update',\n"
    "                                 'params': {'sessionId': 's', 'update': update}}) + '\\n')\n"
    "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': {}}) + '\\n')\n"
    "    sys.stdout.flush()\n"
)


def _chatty(tmp_path: Path, size: int, **kwargs: Any) -> AcpClient:
    script = tmp_path / "chatty.py"
    script.write_text(_CHATTY, encoding="utf-8")
    return AcpClient([sys.executable, str(script)], cwd=str(tmp_path), env={"SIZE": str(size)}, **kwargs)


@pytest.mark.asyncio
async def test_a_tool_output_longer_than_64_kib_is_read_and_the_reply_after_it_too(tmp_path: Path) -> None:
    """The prod verifier on the dev tenant (#498): a `tool_call_update` carrying a file it read was
    longer than asyncio's 64 KiB line limit; the read loop died on it, the answer to
    `session/prompt` was never read, and the run waited 900 s before exiting 1."""
    sizes: list[int] = []

    async def on_update(params: dict[str, Any]) -> None:
        sizes.append(len(params["update"]["rawOutput"]["output"]))

    client = _chatty(tmp_path, 300_000, on_update=on_update)
    async with client:
        response = await client.request(INITIALIZE, {"protocolVersion": 1}, timeout=10.0)
    assert not response.failed
    assert sizes == [300_000]


@pytest.mark.asyncio
async def test_a_message_past_the_limit_is_dropped_and_the_runner_keeps_reading(tmp_path: Path) -> None:
    """Past `max_message_bytes`, one update is lost — never the agent: the next reply still arrives."""
    client = _chatty(tmp_path, 20_000, max_message_bytes=4096)
    async with client:
        response = await client.request(INITIALIZE, {"protocolVersion": 1}, timeout=10.0)
    assert not response.failed
    assert client.dropped_messages == 1


@pytest.mark.asyncio
async def test_a_dead_read_loop_fails_the_request_at_once_and_stop_does_not_hide_it(tmp_path: Path) -> None:
    """A read loop that dies leaves nobody to deliver the reply: the request fails NOW with the
    reason, instead of waiting out its timeout; and `stop()` does not raise the loop's error
    again over the run's own (#498: a bare traceback, exit 1)."""
    import time

    from tranibor_runner.acp.client import AgentUnreachableError

    async def explode(params: dict[str, Any]) -> None:
        raise RuntimeError("handler broken")

    client = _chatty(tmp_path, 10, on_update=explode)
    start = time.monotonic()
    async with client:
        with pytest.raises(AgentUnreachableError) as refusal:
            await client.request(INITIALIZE, {"protocolVersion": 1}, timeout=30.0)
    assert time.monotonic() - start < 15, "the request waited out its timeout"
    assert "handler broken" in str(refusal.value)
    assert "chatty.py" in str(refusal.value)
