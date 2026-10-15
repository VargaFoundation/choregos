"""headroom in a run: started by the runner, on loopback, between the agent and the gateway.

ADR 0055, S37-07. The proxy is a scriptable fake (`fixtures/fake_headroom.py`) taking the real
one's arguments; the gateway is a local HTTP server that writes down what reaches it. A last test
runs the real headroom when it is installed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import StageContext, StageInput
from choregos_runner import compression
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient, agent_script, valid_result

FAKE_HEADROOM = Path(__file__).parent / "fixtures" / "fake_headroom.py"
#: Every `--*-api-url` of `headroom proxy --help`, 0.39.1.
HEADROOM_UPSTREAMS = {
    "--anthropic-api-url",
    "--openai-api-url",
    "--gemini-api-url",
    "--cloudcode-api-url",
    "--vertex-api-url",
    "--bedrock-api-url",
}


class Gateway:
    """The gateway: answers every POST and writes down its path and headers."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        calls = self.calls

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                headers = {k.lower(): v for k, v in self.headers.items()}
                calls.append({"path": self.path, "headers": headers, "body": body})
                data = b'{"id": "msg-1"}'
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: Any) -> None:
                return None

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def gateway() -> Iterator[Gateway]:
    served = Gateway()
    yield served
    served.server.shutdown()


@pytest.fixture
def headroom(tmp_path: Path, runner_settings: Any) -> Any:
    """Scripts the fake headroom, and tells the runner to start it. Returns where it writes down
    its start."""

    # The platform's tool server is not under test here: a remote address keeps the runner from
    # starting it on 7777, a port every run test shares.
    runner_settings.tools_url = "https://tools.invalid/mcp"

    def script(**behaviour: Any) -> Path:
        record = tmp_path / "headroom-start.json"
        path = tmp_path / "headroom-script.json"
        path.write_text(json.dumps({"record": str(record), **behaviour}), encoding="utf-8")
        runner_settings.headroom_command = f"{sys.executable} {FAKE_HEADROOM} {path}"
        return record

    return script


def _compressed(stage_input: StageInput, gateway: Gateway, **context: Any) -> StageInput:
    stage_input.context = StageContext(compression="headroom", **context)
    stage_input.model.base_url = gateway.url
    return stage_input


MODEL_CALL = {
    "base_env": "OPENAI_BASE_URL",
    "key_env": "OPENAI_API_KEY",
    "path": "/chat/completions",
    "body": {"messages": [{"role": "user", "content": "total"}]},
}


async def _run(
    stage_input: StageInput, runner_settings: Any, turns: list[dict[str, Any]] | None = None
) -> tuple[Any, FakeInternalClient, Runner]:
    turn = {
        "model_calls": [MODEL_CALL],
        "writes": [{"path": "src/orders.py", "content": "def total(lines):\n    return 0\n"}],
        "result": valid_result(),
    }
    agent_script({"turns": turns or [turn]}, stage_input, runner_settings.workspace)
    client = FakeInternalClient(stage_input)
    runner = Runner(runner_settings, client)  # type: ignore[arg-type]
    outcome = await runner.execute(stage_input, client)  # type: ignore[arg-type]
    return outcome, client, runner


def _events(client: FakeInternalClient, kind: str) -> list[dict[str, Any]]:
    return [event["payload"] for event in client.events if event["type"] == kind]


async def test_without_compression_nothing_starts_and_the_agent_talks_to_the_gateway(
    stage_input: StageInput, runner_settings: Any, gateway: Gateway, headroom: Any
) -> None:
    record = headroom()
    stage_input.model.base_url = gateway.url
    outcome, client, runner = await _run(stage_input, runner_settings)
    assert outcome.exit_code is Exit.OK, outcome.detail
    assert not record.exists()
    assert runner.compressor is None
    assert [call["path"] for call in gateway.calls] == ["/v1/chat/completions"]
    assert "x-fake-headroom" not in gateway.calls[0]["headers"]
    assert _events(client, "context.compression") == _events(client, "context.degraded") == []


async def test_the_agent_reaches_the_gateway_through_the_run_s_proxy(
    stage_input: StageInput, runner_settings: Any, gateway: Gateway, headroom: Any
) -> None:
    record = headroom()
    outcome, client, runner = await _run(
        _compressed(stage_input, gateway, headroom_profile="balanced"), runner_settings
    )
    assert outcome.exit_code is Exit.OK, outcome.detail

    # Through the proxy, with the run's key unchanged.
    [call] = gateway.calls
    assert call["path"] == "/v1/chat/completions"
    assert call["headers"]["x-fake-headroom"] == "1"
    assert call["headers"]["x-api-key"] == "sk-fake"
    assert _events(client, "context.compression") == [{"compression": "headroom", "profile": "balanced"}]

    started = json.loads(record.read_text(encoding="utf-8"))
    argv = started["argv"]
    assert argv[:5] == ["proxy", "--host", "127.0.0.1", "--port", argv[4]]
    assert {"--stateless", "--no-telemetry"} <= set(argv)
    assert argv[argv.index("--mode") + 1] == "cache"
    # Every upstream headroom 0.39.1 knows is the gateway: none is left on a provider's public
    # endpoint, with the run's key.
    upstreams = {argv[i]: argv[i + 1] for i in range(len(argv) - 1) if argv[i].endswith("-api-url")}
    assert upstreams == dict.fromkeys(HEADROOM_UPSTREAMS, gateway.url)

    # Offline, profiled, and given nothing of the runner's: no key, no run token.
    env = started["env"]
    assert env["HEADROOM_OFFLINE"] == "1"
    assert env["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"
    assert env["HEADROOM_SAVINGS_PROFILE"] == "balanced"
    assert not any(key.startswith(("CHOREGOS_", "OPENAI_", "ANTHROPIC_")) for key in env), sorted(env)
    assert not {"sk-fake", runner_settings.run_token} & set(env.values())

    # It works outside the workspace, and dies with the run.
    assert Path(started["cwd"]) == compression.proxy_home(stage_input)
    workspace = runner_settings.workspace
    tracked = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert tracked == ["src/orders.py"], tracked
    assert runner.compressor is not None and runner.compressor.process.returncode is not None


async def test_a_proxy_that_does_not_start_is_bypassed_and_said(
    stage_input: StageInput, runner_settings: Any, gateway: Gateway, headroom: Any
) -> None:
    headroom(exit=3)
    outcome, client, _ = await _run(_compressed(stage_input, gateway), runner_settings)
    assert outcome.exit_code is Exit.OK, outcome.detail
    [degraded] = _events(client, "context.degraded")
    assert degraded["compression"] == "headroom"
    assert degraded["on_unavailable"] == "bypass"
    assert "told to exit" in degraded["reason"]
    # The agent went to the gateway directly.
    [call] = gateway.calls
    assert "x-fake-headroom" not in call["headers"]
    assert call["headers"]["x-api-key"] == "sk-fake"
    assert _events(client, "context.compression") == []


async def test_a_proxy_that_does_not_start_fails_the_stage_when_the_policy_says_so(
    stage_input: StageInput, runner_settings: Any, gateway: Gateway, headroom: Any
) -> None:
    headroom(exit=3)
    outcome, client, _ = await _run(_compressed(stage_input, gateway, on_unavailable="fail"), runner_settings)
    assert outcome.exit_code is Exit.CONTEXT_UNAVAILABLE
    assert "told to exit" in outcome.detail
    assert [e["phase"] for e in _events(client, "run.failed")] == ["context"]
    assert gateway.calls == []
    assert client.results == []


async def test_the_proxy_is_stopped_when_the_run_ends_badly(
    stage_input: StageInput, runner_settings: Any, gateway: Gateway, headroom: Any
) -> None:
    headroom()
    stage_input = _compressed(stage_input, gateway)
    agent_script({"fail_initialize": True}, stage_input, runner_settings.workspace)
    client = FakeInternalClient(stage_input)
    runner = Runner(runner_settings, client)  # type: ignore[arg-type]
    outcome = await runner.execute(stage_input, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.AGENT_UNREACHABLE
    assert runner.compressor is not None and runner.compressor.process.returncode is not None


async def test_a_proxy_that_never_answers_is_killed(
    stage_input: StageInput, gateway: Gateway, headroom: Any, runner_settings: Any
) -> None:
    record = headroom(silent=True)
    with pytest.raises(compression.CompressionUnavailableError, match="did not answer within 1 s"):
        await compression.start(
            _compressed(stage_input, gateway), command=runner_settings.headroom_command, timeout=1
        )
    pid = json.loads(record.read_text(encoding="utf-8"))["pid"]
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"api_format": "gemini"}, "not wired for the `gemini` format"),
        ({"base_url": "http://litellm:4000/gateway"}, "path headroom cannot forward"),
    ],
)
async def test_what_headroom_cannot_carry_is_unavailable(
    stage_input: StageInput, change: dict[str, Any], reason: str
) -> None:
    stage_input.context = StageContext(compression="headroom")
    stage_input.model = stage_input.model.model_copy(update=change)
    with pytest.raises(compression.CompressionUnavailableError, match=reason):
        await compression.start(stage_input, command="/nonexistent/headroom")


async def test_without_a_run_key_there_is_no_gateway_to_put_it_in_front_of(stage_input: StageInput) -> None:
    stage_input.context = StageContext(compression="headroom")
    stage_input.gateway_key = None
    with pytest.raises(compression.CompressionUnavailableError, match="no gateway key"):
        await compression.start(stage_input, command="/nonexistent/headroom")


def test_the_agent_keeps_the_gateway_s_path() -> None:
    assert compression._local_url("http://litellm:4000", 9) == "http://127.0.0.1:9"
    assert compression._local_url("http://litellm:4000/v1/", 9) == "http://127.0.0.1:9/v1"


HEADROOM = shutil.which("headroom")


@pytest.mark.skipif(HEADROOM is None, reason="headroom is not installed (the runner image carries it)")
async def test_the_real_headroom_forwards_to_the_gateway_with_the_run_s_key(
    stage_input: StageInput, gateway: Gateway
) -> None:
    """Against the binary: it starts offline, answers `/livez`, and an Anthropic request with a
    large tool result reaches the gateway with the run's key."""
    stage_input = _compressed(stage_input, gateway)
    stage_input.model.api_format = "anthropic"  # type: ignore[assignment]
    stage_input.model.base_url = gateway.url.removesuffix("/v1")
    listed = subprocess.run(
        [str(HEADROOM), "proxy", "--help"], capture_output=True, text=True, check=True, timeout=120
    ).stdout
    assert set(re.findall(r"--[a-z-]+-api-url\b", listed)) == HEADROOM_UPSTREAMS
    compressor = await compression.start(stage_input, command=str(HEADROOM), timeout=180)
    try:
        lines = "\n".join(f'{{"id": {i}, "status": "ok", "total": {i * 3}}}' for i in range(400))
        body = {
            "model": "claude-sonnet",
            "max_tokens": 16,
            "messages": [
                {"role": "user", "content": "list the orders"},
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}],
                },
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": lines}]},
            ],
        }
        import httpx

        async with httpx.AsyncClient(timeout=60) as http:
            await http.post(
                f"{compressor.url}/v1/messages",
                json=body,
                headers={"x-api-key": "sk-fake", "anthropic-version": "2023-06-01"},
            )
    finally:
        await compression.stop(compressor)
    assert gateway.calls, compression._tail(compressor.log, 2000)
    call = gateway.calls[-1]
    assert call["path"] == "/v1/messages"
    assert call["headers"].get("x-api-key") == "sk-fake"
