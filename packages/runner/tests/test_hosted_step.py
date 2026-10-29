"""A step carried by a hosted agent (ADR 0080): the agent's own container, scripted, beside the runner.

The container is a `FakeHostedAgent`: the runner reaches it in memory, and it reaches the platform the
way a real one does — over real loopback sockets, with nothing but the environment `sidecar_env`
gives it. Without the `hosted` branch the runner asks the `hosted` backend for a launch plan and the
step never reaches the container. The other tests are the walls: `max_turns` model calls, the granted
MCP servers and the guard rails, the step's network, and no credential in the container.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import pytest
from choregos_adapters.fakes import FakeHostedAgent, FakeModelServer
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_contracts import (
    AgentRef,
    HostedCall,
    McpServerRef,
    ModelRef,
    StageInput,
    StageResult,
    StageStatus,
    ToolsRef,
)
from choregos_core.hosted import sidecar_env
from choregos_runner.exits import Exit
from choregos_runner.loopback.connect import ConnectProxy
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient, loopback_ports, port_of

pytestmark = pytest.mark.asyncio

#: Built at run time: a literal that looks like a key would trip the secrets scan of the CI.
KEY = "-".join(["sk", "run", "7c1e" * 4])
CRM_SECRET = "-".join(["crm", "9f3a" * 4])
CRM = "http://crm.internal/mcp"
IMAGE = "registry.example/acme/triage@sha256:" + "a" * 64


def _hosted(stage_input: StageInput, *, outputs: list[str] | None = None, **update: Any) -> StageInput:
    no_repo = stage_input.project.model_copy(
        update={"test_command": "", "lint_command": "", "typecheck_command": ""}
    )
    hosted = update.pop("hosted", None) or HostedCall(image=IMAGE, agent_slug="triage", agent_version=3)
    return stage_input.model_copy(
        update={
            "repo": None,
            "project": no_repo,
            "agent": AgentRef(backend="hosted"),
            "model": ModelRef(litellm_model="platform/standard", base_url="http://gateway:4000/v1"),
            "gateway_key": KEY,
            "hosted": hosted,
            "budget": stage_input.budget.model_copy(update={"max_turns": 2}),
            "transition": stage_input.transition.model_copy(
                update={"role": "triage", "outputs": outputs or []}
            ),
            **update,
        }
    )


async def _run(
    stage_input: StageInput,
    agent: FakeHostedAgent,
    settings: Any,
    model: FakeModelServer | None = None,
    mcp: FakeMcpServer | None = None,
) -> tuple[Any, FakeInternalClient]:
    """Runs the step with `agent` as its container, its environment the one the executor gives it."""
    client = FakeInternalClient(stage_input)
    runner = Runner(settings, client)  # type: ignore[arg-type]
    # The runner binds its tools server before the loopback: no loopback port may be that one.
    runner.loopback_ports = loopback_ports(avoid=[port_of(settings.tools_url)])
    runner.readiness_interval = 0.05
    runner.hosted_transport = agent.transport()
    runner.model_transport = (model or FakeModelServer(key=KEY)).transport()
    runner.mcp_transport = (mcp or FakeMcpServer()).transport()
    assert stage_input.hosted is not None
    agent.env = sidecar_env(
        stage_input.hosted,
        run_id=stage_input.run_id,
        model=stage_input.model.litellm_model,
        mcp_servers=stage_input.tools.mcp,
        ports=runner.loopback_ports,
    )
    outcome = await runner.execute(stage_input, client)  # type: ignore[arg-type]
    return outcome, client


def _events(client: FakeInternalClient, type_: str) -> list[dict[str, Any]]:
    return [event["payload"] for event in client.events if event["type"] == type_]


async def test_a_hosted_agent_carries_the_step_and_its_json_answer_is_the_result(
    stage_input: StageInput, runner_settings: Any
) -> None:
    model = FakeModelServer(key=KEY).say("M, low risk.")
    agent = FakeHostedAgent(
        script=[("model", "size this")],
        answer={"status": "done", "summary": "Sized M.", "outputs": {"size": "M"}},
    )
    outcome, client = await _run(_hosted(stage_input, outputs=["size"]), agent, runner_settings, model)

    assert outcome.exit_code is Exit.OK
    result = client.results[-1]
    StageResult.model_validate(result.model_dump(mode="json", by_alias=True))
    assert result.status is StageStatus.DONE
    assert result.outputs.model_dump(exclude_none=True)["size"] == "M"
    assert result.diagnostics.turns == 1
    assert result.artifacts.reports["hosted_provenance"] == "hosted:triage@3"
    assert result.artifacts.reports["hosted_image"] == IMAGE
    assert result.artifacts.reports["hosted_response_id"] == "resp_1"
    assert "Corriger le total" in agent.requests[0]["input"], "the task reaches the container"
    assert agent.requests[0]["metadata"]["choregos_run_id"] == "run-test-1"
    assert agent.seen[0][0] == 200
    headers, body = model.requests[0]
    assert body["model"] == "platform/standard", "the run's model, whatever the container named"
    assert headers["authorization"] == f"Bearer {KEY}"
    assert [e["status"] for e in _events(client, "hosted.answered")] == ["answered"]


async def test_a_text_answer_is_the_summary_of_a_step_that_declares_no_output(
    stage_input: StageInput, runner_settings: Any
) -> None:
    hosted = HostedCall(image=IMAGE, agent_slug="triage", agent_version=3, protocol="invocations")
    agent = FakeHostedAgent(answer="Nothing to triage.")
    outcome, client = await _run(_hosted(stage_input, hosted=hosted), agent, runner_settings)

    assert outcome.exit_code is Exit.OK
    assert (client.results[-1].status, client.results[-1].summary) == (StageStatus.DONE, "Nothing to triage.")


async def test_a_text_answer_to_a_step_that_declares_outputs_is_refused(
    stage_input: StageInput, runner_settings: Any
) -> None:
    agent = FakeHostedAgent(answer="It is an M.")
    outcome, client = await _run(_hosted(stage_input, outputs=["size"]), agent, runner_settings)

    assert outcome.exit_code is Exit.INVALID_RESULT
    assert (client.results[-1].status, client.results[-1].reason) == (
        StageStatus.FAILED,
        "hosted_result_invalid",
    )
    assert "answer a JSON result" in client.results[-1].summary, "the container is told what to answer"


async def test_a_container_that_is_never_ready_fails_the_step(
    stage_input: StageInput, runner_settings: Any
) -> None:
    hosted = HostedCall(image=IMAGE, agent_slug="triage", agent_version=3, readiness_timeout_s=1)
    agent = FakeHostedAgent(ready_after=None)
    outcome, client = await _run(_hosted(stage_input, hosted=hosted), agent, runner_settings)

    assert outcome.exit_code is Exit.AGENT_UNREACHABLE
    result = client.results[-1]
    assert (result.status, result.reason) == (StageStatus.FAILED, "hosted_not_ready")
    assert agent.requests == [], "nothing is sent to a container that never said it was ready"
    assert agent.probes > 1


async def test_a_container_that_becomes_ready_gets_the_step(
    stage_input: StageInput, runner_settings: Any
) -> None:
    agent = FakeHostedAgent(ready_after=3, answer="Done.")
    _, client = await _run(_hosted(stage_input), agent, runner_settings)

    assert agent.probes == 4 and len(agent.requests) == 1
    assert client.results[-1].status is StageStatus.DONE


async def test_a_failed_response_fails_the_step(stage_input: StageInput, runner_settings: Any) -> None:
    agent = FakeHostedAgent(fail=True)
    outcome, client = await _run(_hosted(stage_input), agent, runner_settings)

    assert outcome.exit_code is Exit.INVALID_RESULT
    result = client.results[-1]
    assert (result.status, result.reason) == (StageStatus.FAILED, "hosted_failed")
    assert "the agent crashed" in result.summary


async def test_the_model_call_past_max_turns_never_reaches_the_gateway(
    stage_input: StageInput, runner_settings: Any
) -> None:
    model = FakeModelServer(key=KEY).say("one").say("two").say("three")
    agent = FakeHostedAgent(script=[("model", "a"), ("model", "b"), ("model", "c")], answer="Done.")
    _, client = await _run(_hosted(stage_input), agent, runner_settings, model)

    assert len(model.requests) == 2, "max_turns is 2"
    assert [status for status, _ in agent.seen] == [200, 200, 400], "400: no SDK retries it"
    assert agent.seen[2][1]["error"]["type"] == "max_turns"
    assert _events(client, "hosted.model_refused") == [
        {"path": "chat/completions", "reason": "max_turns", "limit": 2}
    ]
    result = client.results[-1]
    assert result.diagnostics.turns == 2 and result.diagnostics.agent_exit == "limit"


async def test_the_loopback_model_accepts_the_loopback_key_only(
    stage_input: StageInput, runner_settings: Any
) -> None:
    model = FakeModelServer(key=KEY).say("one")
    agent = FakeHostedAgent(script=[("model", "a")], answer="Done.")
    stage = _hosted(stage_input)
    client = FakeInternalClient(stage)
    runner = Runner(runner_settings, client)  # type: ignore[arg-type]
    runner.loopback_ports = loopback_ports(avoid=[port_of(runner_settings.tools_url)])
    runner.hosted_transport = agent.transport()
    runner.model_transport = model.transport()
    assert stage.hosted is not None
    agent.env = {
        **sidecar_env(stage.hosted, run_id="run-test-1", model="m", ports=runner.loopback_ports),
        "OPENAI_API_KEY": KEY,
    }
    await runner.execute(stage, client)  # type: ignore[arg-type]

    assert agent.seen[0][0] == 401
    assert model.requests == [], "a key other than the loopback's is never forwarded"


async def test_a_spent_key_stops_the_step_on_budget(stage_input: StageInput, runner_settings: Any) -> None:
    refusal = {"error": {"message": "Budget has been exceeded! Current cost: 5.1", "type": "budget_exceeded"}}
    model = FakeModelServer(key=KEY).refuse(400, refusal)
    agent = FakeHostedAgent(script=[("model", "a")], answer="Done anyway.")
    outcome, client = await _run(_hosted(stage_input), agent, runner_settings, model)

    assert outcome.exit_code is Exit.BUDGET_EXCEEDED
    assert agent.seen[0][0] == 400, "the container sees the gateway's refusal"
    assert client.results[-1].status is StageStatus.FAILED


async def test_an_mcp_call_carries_the_servers_credential_the_container_never_holds(
    stage_input: StageInput, runner_settings: Any
) -> None:
    crm = FakeMcpServer(outils=[{"name": "lookup", "description": "", "inputSchema": {"type": "object"}}])
    crm.jeton = CRM_SECRET
    agent = FakeHostedAgent(script=[("mcp", "crm", "lookup", {"customer": "acme"})], answer="Acme.")
    tools = ToolsRef(mcp={"crm": McpServerRef(url=CRM, env={"Authorization": f"Bearer {CRM_SECRET}"})})
    _, client = await _run(_hosted(stage_input, tools=tools), agent, runner_settings, mcp=crm)

    assert crm.appels == [("lookup", {"customer": "acme"})]
    assert agent.seen[0][1]["result"]["content"][0]["text"] == "lookup fait"
    assert agent.env["CHOREGOS_MCP_SERVERS"] == "crm"
    assert client.results[-1].diagnostics.tool_calls == 1
    assert [e["tool"] for e in _events(client, "session/request_permission")] == ["crm/lookup"]


async def test_an_mcp_server_the_step_was_not_granted_is_not_served(
    stage_input: StageInput, runner_settings: Any
) -> None:
    crm = FakeMcpServer(outils=[{"name": "lookup", "description": "", "inputSchema": {"type": "object"}}])
    agent = FakeHostedAgent(script=[("mcp", "erp", "lookup", {})], answer="Nothing.")
    tools = ToolsRef(mcp={"crm": McpServerRef(url=CRM)})
    _, client = await _run(_hosted(stage_input, tools=tools), agent, runner_settings, mcp=crm)

    assert agent.seen[0][0] == 404
    assert crm.recues == []
    assert _events(client, "hosted.mcp_refused") == [{"server": "erp", "reason": "not granted"}]


async def test_an_mcp_call_the_guard_rails_refuse_never_reaches_the_server(
    stage_input: StageInput, runner_settings: Any
) -> None:
    crm = FakeMcpServer(outils=[{"name": "run", "description": "", "inputSchema": {"type": "object"}}])
    agent = FakeHostedAgent(
        script=[("mcp", "crm", "run", {"command": "kubectl delete ns prod"})], answer="Ok."
    )
    tools = ToolsRef(mcp={"crm": McpServerRef(url=CRM)})
    _, client = await _run(_hosted(stage_input, tools=tools), agent, runner_settings, mcp=crm)

    assert crm.appels == []
    answer = agent.seen[0][1]["result"]
    assert answer["isError"] and "refused by the run's guard rails" in answer["content"][0]["text"]
    assert client.results[-1].diagnostics.permission_denials == 1


async def test_an_mcp_server_on_a_denied_host_is_never_reached(
    stage_input: StageInput, runner_settings: Any
) -> None:
    crm = FakeMcpServer(outils=[{"name": "lookup", "description": "", "inputSchema": {"type": "object"}}])
    agent = FakeHostedAgent(script=[("mcp", "crm", "lookup", {})], answer="Nothing.")
    permissions = stage_input.permissions.model_copy(update={"deny_domains": ["crm.internal"]})
    tools = ToolsRef(mcp={"crm": McpServerRef(url=CRM)})
    stage = _hosted(stage_input, tools=tools, permissions=permissions)
    _, client = await _run(stage, agent, runner_settings, mcp=crm)

    assert agent.seen[0][0] == 403
    assert crm.recues == []
    assert _events(client, "hosted.mcp_refused")[0]["server"] == "crm"


async def test_a_tunnel_to_a_denied_host_is_refused(stage_input: StageInput, runner_settings: Any) -> None:
    agent = FakeHostedAgent(script=[("tunnel", "evil.example", 443)], answer="Done.")
    permissions = stage_input.permissions.model_copy(update={"deny_domains": ["*.example"]})
    _, client = await _run(_hosted(stage_input, permissions=permissions), agent, runner_settings)

    assert agent.seen == ["HTTP/1.1 403 Forbidden"]
    assert _events(client, "hosted.egress_refused") == [
        {"host": "evil.example", "port": 443, "reason": "denied"}
    ]


async def test_a_listed_network_refuses_a_host_outside_its_list(
    stage_input: StageInput, runner_settings: Any
) -> None:
    agent = FakeHostedAgent(script=[("tunnel", "elsewhere.example", 443)], answer="Done.")
    permissions = stage_input.permissions.model_copy(update={"allow_domains": ["api.example"]})
    _, client = await _run(_hosted(stage_input, permissions=permissions), agent, runner_settings)

    assert agent.seen == ["HTTP/1.1 403 Forbidden"]
    assert _events(client, "hosted.egress_refused")[0]["reason"] == "not_allowed"


async def test_a_tunnel_carries_the_runs_credentials_to_the_egress_proxy(
    stage_input: StageInput, runner_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    heads: list[bytes] = []

    async def egress(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        heads.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(egress, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("HTTPS_PROXY", f"http://run-test-1:tok@127.0.0.1:{port}")
    agent = FakeHostedAgent(script=[("tunnel", "api.example", 443)], answer="Done.")
    permissions = stage_input.permissions.model_copy(update={"allow_domains": ["api.example"]})
    async with server:
        _, client = await _run(_hosted(stage_input, permissions=permissions), agent, runner_settings)

    assert agent.seen == ["HTTP/1.1 200 Connection established"]
    head = heads[0].decode()
    assert head.startswith("CONNECT api.example:443 HTTP/1.1")
    assert f"Proxy-Authorization: Basic {base64.b64encode(b'run-test-1:tok').decode()}" in head
    assert _events(client, "hosted.egress") == [{"host": "api.example", "port": 443}]


async def test_no_credential_is_in_the_containers_environment(stage_input: StageInput) -> None:
    hosted = HostedCall(
        image=IMAGE, agent_slug="triage", agent_version=3, env={"LOG_LEVEL": "debug", "PORT": "9999"}
    )
    tools = ToolsRef(mcp={"crm": McpServerRef(url=CRM, env={"Authorization": f"Bearer {CRM_SECRET}"})})
    stage = _hosted(stage_input, hosted=hosted, tools=tools)
    # A run token of its own: the conftest's `tok` is a substring of `/msi/token` (S41-19).
    run_token = "-".join(["run", "token", "9f3a" * 4])
    stage = stage.model_copy(
        update={"callbacks": stage.callbacks.model_copy(update={"run_token": run_token})}
    )
    env = sidecar_env(hosted, run_id=stage.run_id, model="platform/standard", mcp_servers=stage.tools.mcp)

    text = json.dumps(env)
    assert KEY not in text and CRM_SECRET not in text and stage.callbacks.run_token not in text
    assert env["OPENAI_API_KEY"] == env["ANTHROPIC_API_KEY"] == "choregos-loopback"
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:4001/v1"
    assert env["HTTPS_PROXY"] == env["https_proxy"] == "http://127.0.0.1:4003"
    assert env["CHOREGOS_MCP_URL"] == "http://127.0.0.1:4002/mcp/"
    assert (env["LOG_LEVEL"], env["PORT"]) == ("debug", "8088"), "the platform's variables win"


async def test_plain_http_through_the_proxy_is_refused(stage_input: StageInput) -> None:
    recorded: list[tuple[str, dict[str, Any]]] = []

    async def record(type_: str, payload: dict[str, Any]) -> None:
        recorded.append((type_, payload))

    proxy = ConnectProxy(_hosted(stage_input), record)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    async with server:
        reader, writer = await asyncio.open_connection(*server.sockets[0].getsockname()[:2])
        writer.write(b"GET http://api.example/ HTTP/1.1\r\nHost: api.example\r\n\r\n")
        await writer.drain()
        line = (await reader.readline()).decode().strip()
        writer.close()

    assert line == "HTTP/1.1 405 Method Not Allowed"
    assert recorded == [("hosted.egress_refused", {"target": "http://api.example/", "reason": "not_connect"})]
