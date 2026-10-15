"""The seven conformance checks, for an agent written in code (ADR 0063).

A code agent is an ACP agent like any backend, so it must pass the same seven checks before it is
trusted. The checks keep their names; what a code agent is asked to do differs from the shipped
backends' scenario (a scripted agent that writes `hello.py`): a code agent is not scripted, so the
observable is a deliverable the SDK writes through the guard.

| check | what is observed |
| --- | --- |
| initialize | `initialize` answers with the agent's info |
| mcp | `session/new` accepts the run's MCP servers |
| prompt_trivial | one prompt turn ends and leaves a deliverable in `.choregos/outputs/` |
| permission_denied | when the guard refuses every write, the deliverable is not written |
| result_valid | `.choregos/result.json` is valid after the turn, accepted and refused alike |
| max_turns | the agent's first turn made no more calls (tool and model calls) than `max_turns` |
| cost_at_gateway | the run's key is in the agent's environment; the gateway saw no other key |

The agents under test come from `CHOREGOS_CONFORMANCE_CODE_AGENTS`, JSON `{name: argv}` or
`{name: {"image": ref, "command": argv}}` (the agent then runs in that image, through `docker run -i`);
without it, the plain example runs. `make conformance-agent IMAGE=<ref>` sets it for one image.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import StageInput
from choregos_runner.acp import AcpClient, AgentUnreachableError
from choregos_runner.acp.guard import TurnGuard
from choregos_runner.backends import get_backend
from choregos_runner.result import load_result

from .test_acp_conformance import CHECKS, ConformanceResult

pytestmark = pytest.mark.conformance

RUN_KEY = "run-" + "c" * 12
EXAMPLES = Path(__file__).resolve().parents[3] / "examples" / "agents" / "plain" / "src"
#: The plain example is not a workspace member (the images would have to carry it): it runs from its source.
DEFAULT_AGENTS: dict[str, Any] = {
    "plain": ["env", f"PYTHONPATH={EXAMPLES}", sys.executable, "-m", "choregos_agent_examples.plain"]
}


def configured_agents() -> dict[str, Any]:
    raw = os.environ.get("CHOREGOS_CONFORMANCE_CODE_AGENTS")
    return json.loads(raw) if raw else DEFAULT_AGENTS


def container_command(image: str, command: list[str], workspace: Path, env: dict[str, str]) -> list[str]:
    """`docker run -i` for an agent that lives in an image: the workspace is mounted at the same path
    (the agent writes `.choregos/` there), the environment is passed variable by variable, and the
    container shares the host's network (the fake gateway listens on the host)."""
    flags: list[str] = []
    for name in sorted(env):
        flags += ["-e", name]
    return [
        "docker", "run", "-i", "--rm", "--network=host",
        "-v", f"{workspace}:{workspace}", "-w", str(workspace), *flags, image, *command,
    ]  # fmt: skip


@dataclass
class FakeGateway:
    url: str
    seen: list[str] = field(default_factory=list)


@pytest.fixture
def gateway() -> Iterator[FakeGateway]:
    """An OpenAI-compatible endpoint that records the Authorization header of each call."""
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("content-length", 0)))
            seen.append(self.headers.get("authorization", ""))
            body = json.dumps(
                {
                    "id": "c1",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "m",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield FakeGateway(url=f"http://127.0.0.1:{server.server_address[1]}/v1", seen=seen)
    server.shutdown()


def code_stage_input(command: list[str], gateway_url: str, workspace: Path) -> StageInput:
    from choregos_contracts import (
        AgentRef,
        Budget,
        Callbacks,
        LaunchSpec,
        ModelRef,
        Permissions,
        PlaybookRef,
        ProjectRef,
        TransitionRef,
        WorkItemRef,
    )

    return StageInput(
        run_id="conformance-code-1",
        attempt=1,
        project=ProjectRef(slug="conformance", org="varga"),
        work_item=WorkItemRef(key="varga/conformance#2", title="Checkout fails with an error after payment"),
        transition=TransitionRef(id="t-triage", role="triage", **{"from": "inbox"}, to="triaged"),
        agent=AgentRef(backend="code", launch=LaunchSpec(command=command)),
        model=ModelRef(litellm_model="platform/standard", base_url=gateway_url, api_format="openai"),
        gateway_key=RUN_KEY,
        budget=Budget(usd=1, max_turns=3, max_minutes=2),
        allowed_paths=["**"],
        playbook=PlaybookRef(ref="triage@conformance", prompt="Triage the ticket."),
        permissions=Permissions(write_paths=["**"], max_findings=1),
        callbacks=Callbacks(api_url="http://localhost:0/internal", run_token="tok"),
    )


async def run_code_suite(name: str, spec: Any, gateway: FakeGateway, workspace: Path) -> ConformanceResult:
    """The seven checks against one code agent (see the module's table)."""
    result = ConformanceResult(backend=f"code:{name}")
    argv, image = (spec["command"], spec["image"]) if isinstance(spec, dict) else (list(spec), None)
    stage_input = code_stage_input(argv, gateway.url, workspace)
    backend = get_backend("code")
    plan = backend.launch_plan(stage_input, workspace)
    env = {**plan.env, **backend.model_env(stage_input.model, stage_input.gateway_key)}
    command = container_command(image, plan.command, workspace, env) if image else plan.command
    outputs = workspace / ".choregos" / "outputs"
    result_path = workspace / ".choregos" / "result.json"
    deny_writes = False
    decisions: list[tuple[str, bool]] = []

    async def on_permission(params: dict[str, Any]) -> tuple[bool, str]:
        call = params.get("toolCall") or {}
        refuse = deny_writes and call.get("kind") == "edit"
        decisions.append((str(call.get("kind")), not refuse))
        return (False, "refused by the conformance suite") if refuse else (True, "ok")

    def verdict(check: str, ok: bool, why: str) -> None:
        if ok:
            result.passed.append(check)
        else:
            result.failed[check] = why

    # The runner's own guard counts the agent's calls, as it does in a run.
    guard = TurnGuard(max_turns=stage_input.budget.max_turns)
    client = AcpClient(command, cwd=str(workspace), env=env, on_permission=on_permission, guard=guard)
    try:
        async with client:
            info = await client.initialize()
            verdict("initialize", bool(info.get("agentInfo")), "no agentInfo in the answer")
            session = await client.new_session(str(workspace), plan.mcp_servers)
            verdict("mcp", bool(session), "session/new refused")

            first = await client.prompt(stage_input.work_item.title, timeout=90)
            delivered = outputs.is_dir() and any(outputs.iterdir())
            verdict(
                "prompt_trivial",
                first.stop_reason == "end_turn" and delivered,
                f"stop reason {first.stop_reason!r}, deliverable {'present' if delivered else 'missing'}: "
                f"{client.stderr_tail[-3:]}",
            )
            first_result = load_result(result_path)
            calls = guard.turns
            verdict(
                "max_turns",
                guard.tripped is None and calls <= stage_input.budget.max_turns,
                f"{calls} calls in one turn, the step allows {stage_input.budget.max_turns}",
            )

            # The limit was judged on the first turn; the second is about refusals, not about turns.
            guard.max_turns = None
            # Same agent, same session, every write now refused.
            shutil.rmtree(outputs, ignore_errors=True)
            deny_writes = True
            await client.prompt(stage_input.work_item.title, timeout=90)
            refused = any(kind == "edit" and not allowed for kind, allowed in decisions)
            verdict(
                "permission_denied",
                refused and not (outputs.is_dir() and any(outputs.iterdir())),
                "the refused write happened anyway, or none was asked",
            )
            second_result = load_result(result_path)

            verdict(
                "result_valid",
                first_result.ok and second_result.ok,
                first_result.error or second_result.error or "unreadable result",
            )

            model_env = backend.model_env(stage_input.model, stage_input.gateway_key)
            reached = [auth for auth in gateway.seen if auth != f"Bearer {RUN_KEY}"]
            verdict(
                "cost_at_gateway",
                any(RUN_KEY in value for value in model_env.values()) and not reached,
                f"the run's key is not the only one the gateway saw: {reached}",
            )
    except AgentUnreachableError as exc:
        result.failed["initialize"] = str(exc)
    return result


@pytest.mark.parametrize("name", sorted(configured_agents()))
async def test_code_agent_passes_all_checks(name: str, gateway: FakeGateway, tmp_path: Path) -> None:
    spec = configured_agents()[name]
    if isinstance(spec, dict) and shutil.which("docker") is None:
        pytest.skip("docker is needed to run an agent in its image")
    workspace = tmp_path / name
    workspace.mkdir()
    result = await run_code_suite(name, spec, gateway, workspace)
    assert result.ok, result.failed
    assert set(result.passed) == set(CHECKS)


async def test_a_code_agent_that_ignores_the_guard_fails_conformance(
    gateway: FakeGateway, tmp_path: Path
) -> None:
    """The suite must be able to fail: an agent that writes its deliverable without asking the
    guard breaks `permission_denied` (this is what skipping the SDK looks like)."""
    rogue = Path(__file__).with_name("rogue_code_agent.py")
    (tmp_path / "ws").mkdir()
    result = await run_code_suite("rogue", [sys.executable, str(rogue)], gateway, tmp_path / "ws")
    assert "permission_denied" in result.failed, result.to_dict()


def test_an_image_agent_runs_through_docker_with_the_workspace_and_the_environment(tmp_path: Path) -> None:
    command = container_command(
        "registry.example/agent@sha256:" + "0" * 64, ["python", "-m", "agent"], tmp_path, {"B": "1", "A": "2"}
    )
    assert command[:5] == ["docker", "run", "-i", "--rm", "--network=host"]
    assert ["-v", f"{tmp_path}:{tmp_path}", "-w", str(tmp_path)] == command[5:9]
    assert command[9:13] == ["-e", "A", "-e", "B"], "variables are passed by name, never by value"
    assert command[-3:] == ["python", "-m", "agent"]
