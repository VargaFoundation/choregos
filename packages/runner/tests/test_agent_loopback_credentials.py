"""The agent in its own container holds no credential (ADR 0146 §7, S65-04f).

The gateway key, the egress proxy's token and an MCP server's headers stay in the runner. The agent is
handed a COPY of the step that names the runner's loopback listeners and the constant loopback key; the
listeners forward with the real values. The model listener counts no turn for an ACP step (the turn guard
keeps `max_turns`), the gateway keeps enforcing the run's budget, and the project's checks get the same
proxy variables as the agent.
"""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any

import httpx
import pytest
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_contracts import McpServerRef, StageInput
from choregos_core.hosted import LOOPBACK_KEY, loopback_url
from choregos_runner.backends import get_backend
from choregos_runner.exits import Exit
from choregos_runner.guardrails import GuardRails
from choregos_runner.loopback.agent import agent_view, proxy_environment
from choregos_runner.loopback.mcp import McpProxy
from choregos_runner.loopback.model import ModelProxy
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient, agent_script, loopback_ports, port_of, valid_result
from .test_agent_holds_no_run_token import _read_probe
from .test_agent_isolation import _settings, serving  # noqa: F401  (a fixture)

pytestmark = pytest.mark.asyncio

#: Built at run time: a literal that looks like a key would trip the secrets scan of the CI.
GATEWAY = "-".join(["sk", "run", "9c1e" * 4])
EGRESS = "-".join(["egress", "tok", "7f3a" * 4])
MCP_HEADER = "-".join(["mcp", "secret", "2d5b" * 4])
PORTS = (4101, 4102, 4103)
PROXY_URL = f"http://run-test-1:{EGRESS}@egress.internal:3128"


def _step(stage_input: StageInput, *, backend: str = "codex") -> StageInput:
    return stage_input.model_copy(
        update={
            "gateway_key": GATEWAY,
            "agent": stage_input.agent.model_copy(update={"backend": backend}),
            "tools": stage_input.tools.model_copy(
                update={
                    "mcp": {
                        "crm": McpServerRef(url="http://crm.internal/mcp", env={"Authorization": MCP_HEADER}),
                        "public": McpServerRef(url="http://docs.internal/mcp"),
                    }
                }
            ),
        }
    )


# ───────────────────────── what the agent is told ─────────────────────────


def test_the_agent_view_names_the_loopback_and_holds_no_secret(stage_input: StageInput) -> None:
    step = _step(stage_input)

    view = agent_view(step, PORTS)

    assert view.gateway_key == LOOPBACK_KEY
    assert view.model.base_url == loopback_url(PORTS[0])
    assert view.tools.mcp["crm"] == McpServerRef(url=f"{loopback_url(PORTS[1])}/mcp/crm")
    assert view.tools.mcp["public"] == step.tools.mcp["public"], "no credential: nothing to hide"
    dumped = view.model_dump_json()
    for secret in (GATEWAY, MCP_HEADER):
        assert secret not in dumped
    assert step.gateway_key == GATEWAY, "the runner's own copy is left as it is"
    assert step.tools.mcp["crm"].env == {"Authorization": MCP_HEADER}


def test_a_backend_that_cannot_speak_to_the_model_listener_keeps_its_key(stage_input: StageInput) -> None:
    """The mechanism stays for a backend that speaks no shape the listener serves; none does today:
    `gemini-cli`'s Gemini paths are served since S65-04f."""
    assert get_backend("gemini-cli").loopback_model is True
    assert get_backend("codex").loopback_model is True
    view = agent_view(_step(stage_input), PORTS, model=False)
    assert view.gateway_key == GATEWAY
    assert view.model.base_url == "http://litellm:4000"
    assert MCP_HEADER not in view.model_dump_json(), "its MCP headers still stay in the runner"


def test_the_proxy_variables_name_the_loopback_proxy_or_nothing() -> None:
    assert proxy_environment({"PATH": "/bin"}, PORTS) == {}, "no proxy to carry a credential"

    env = proxy_environment({"HTTPS_PROXY": PROXY_URL, "NO_PROXY": ".svc"}, PORTS)

    assert {env[n] for n in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")} == {
        loopback_url(PORTS[2])
    }
    assert env["NO_PROXY"].split(",") == ["127.0.0.1", "localhost", ".svc"]
    assert EGRESS not in json.dumps(env)


# ───────────────────────── what the listeners do for it ─────────────────────────


class Gateway:
    def __init__(self, status: int = 200, text: str = "{}") -> None:
        self.requests: list[httpx.Request] = []
        self.status, self.text = status, text

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, text=self.text, headers={"content-type": "application/json"})


async def _client(proxy: ModelProxy) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app()), base_url="http://127.0.0.1:4001")


async def _noop(*_: Any) -> None:
    return None


async def test_the_gateway_gets_the_runs_key_on_a_request_that_carried_the_loopback_key(
    stage_input: StageInput,
) -> None:
    gateway = Gateway()
    step = _step(stage_input)
    proxy = ModelProxy(step, _noop, transport=httpx.MockTransport(gateway), count_turns=False, measure=False)
    async with await _client(proxy) as agent:
        answer = await agent.post(
            "/chat/completions",
            headers={"authorization": f"Bearer {LOOPBACK_KEY}"},
            json={"model": "anything", "messages": []},
        )
        refused = await agent.post(
            "/chat/completions",
            headers={"authorization": f"Bearer {GATEWAY}"},
            json={"model": "anything", "messages": []},
        )

    assert answer.status_code == 200
    [seen] = gateway.requests
    assert seen.headers["authorization"] == f"Bearer {GATEWAY}"
    assert json.loads(seen.content)["model"] == step.model.litellm_model
    assert refused.status_code == 401, "the real key is no credential here: the agent never has it"


async def test_no_model_call_is_counted_as_a_turn_for_an_acp_step(stage_input: StageInput) -> None:
    """`max_turns: 5` is the turn guard's (tool calls); a step makes dozens of model calls."""
    step = _step(stage_input).model_copy(
        update={"budget": stage_input.budget.model_copy(update={"max_turns": 5})}
    )
    headers = {"authorization": f"Bearer {LOOPBACK_KEY}"}
    body = {"model": "m", "messages": []}
    gateway = Gateway()
    acp = ModelProxy(step, _noop, transport=httpx.MockTransport(gateway), count_turns=False, measure=False)
    hosted = ModelProxy(step, _noop, transport=httpx.MockTransport(Gateway()))
    async with await _client(acp) as agent, await _client(hosted) as other:
        acp_answers = [
            (await agent.post("/chat/completions", headers=headers, json=body)).status_code for _ in range(30)
        ]
        hosted_answers = [
            (await other.post("/chat/completions", headers=headers, json=body)).status_code for _ in range(30)
        ]

    assert acp_answers == [200] * 30
    assert len(gateway.requests) == 30, "the 30th request of a 5-turn step is forwarded"
    assert hosted_answers.count(400) == 25, "the hosted listener still counts: the contrast"


async def test_the_gateways_budget_refusal_is_known_to_the_runner(stage_input: StageInput) -> None:
    refusal = "litellm.BudgetExceededError: Budget has been exceeded! Current cost: 5.0012, Max budget: 5.0"
    proxy = ModelProxy(
        _step(stage_input),
        _noop,
        transport=httpx.MockTransport(Gateway(400, json.dumps({"error": {"message": refusal}}))),
        count_turns=False,
        measure=False,
    )
    async with await _client(proxy) as agent:
        answer = await agent.post(
            "/chat/completions", headers={"authorization": f"Bearer {LOOPBACK_KEY}"}, json={"model": "m"}
        )

    assert answer.status_code == 400, "the agent sees the gateway's own refusal"
    assert proxy.budget_refusal and "Budget has been exceeded" in proxy.budget_refusal


GEMINI = "/v1beta/models/gemini-2.5-pro:streamGenerateContent"


async def test_gemini_paths_reach_the_gateway_with_the_runs_key_and_model_and_not_the_loopback_key(
    stage_input: StageInput,
) -> None:
    """S65-04f remainder: `gemini-cli`'s API on the loopback, the key injected runner-side (LiteLLM serves
    `v1beta/models/<m>:<action>` at its root and reads `x-goog-api-key`, checked in litellm 1.105)."""
    gateway = Gateway()
    step = _step(stage_input)
    proxy = ModelProxy(step, _noop, transport=httpx.MockTransport(gateway), count_turns=False, measure=False)
    body = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
    async with await _client(proxy) as agent:
        header = await agent.post(
            GEMINI, params={"alt": "sse"}, headers={"x-goog-api-key": LOOPBACK_KEY}, json=body
        )
        query = await agent.post(
            "/v1beta/models/other-model:generateContent", params={"key": LOOPBACK_KEY}, json=body
        )
        counted = await agent.post(
            "/v1beta/models/x:countTokens", headers={"authorization": f"Bearer {LOOPBACK_KEY}"}, json=body
        )
        refused = [
            await agent.post(GEMINI, headers={"x-goog-api-key": GATEWAY}, json=body),
            await agent.post(GEMINI, json=body),
            await agent.post(GEMINI, params={"key": GATEWAY}, json=body),
            await agent.get(GEMINI, headers={"x-goog-api-key": LOOPBACK_KEY}),
            await agent.post("/v1beta/models/m:embedContent", headers={"x-goog-api-key": LOOPBACK_KEY}),
            await agent.post("/v1beta/files", headers={"x-goog-api-key": LOOPBACK_KEY}),
        ]

    assert [a.status_code for a in (header, query, counted)] == [200, 200, 200]
    assert [a.status_code for a in refused] == [401, 401, 401, 405, 404, 404]
    assert len(gateway.requests) == 3, "nothing refused is forwarded"
    model = step.model.litellm_model
    assert [r.url.path for r in gateway.requests] == [
        f"/v1beta/models/{model}:streamGenerateContent",
        f"/v1beta/models/{model}:generateContent",
        f"/v1beta/models/{model}:countTokens",
    ], "the gateway's root (not under /v1), the run's model whatever the URL named"
    assert gateway.requests[0].url.params["alt"] == "sse"
    for forwarded in gateway.requests:
        assert forwarded.headers["x-goog-api-key"] == GATEWAY
        assert forwarded.headers["authorization"] == f"Bearer {GATEWAY}"
        assert "key" not in forwarded.url.params and LOOPBACK_KEY not in str(forwarded.url)
        assert json.loads(forwarded.content) == body


async def test_gemini_generations_are_counted_as_turns_on_a_hosted_step_and_a_gateway_budget_refusal_is_known(
    stage_input: StageInput,
) -> None:
    step = _step(stage_input).model_copy(
        update={"budget": stage_input.budget.model_copy(update={"max_turns": 2})}
    )
    headers = {"x-goog-api-key": LOOPBACK_KEY}
    hosted = ModelProxy(step, _noop, transport=httpx.MockTransport(Gateway()))
    async with await _client(hosted) as agent:
        answers = [(await agent.post(GEMINI, headers=headers, json={})).status_code for _ in range(4)]
    assert answers == [200, 200, 400, 400]

    refusal = "Budget has been exceeded! Current cost: 5.0012, Max budget: 5.0"
    proxy = ModelProxy(
        step,
        _noop,
        transport=httpx.MockTransport(Gateway(400, json.dumps({"error": {"message": refusal}}))),
        count_turns=False,
        measure=False,
    )
    async with await _client(proxy) as agent:
        answer = await agent.post(GEMINI, headers=headers, json={})
    assert (
        answer.status_code == 400
        and proxy.budget_refusal
        and "Budget has been exceeded" in proxy.budget_refusal
    )


async def test_the_mcp_header_is_added_by_the_runner_and_a_call_from_the_shell_is_still_judged(
    stage_input: StageInput,
) -> None:
    server = FakeMcpServer(tools=[{"name": "ping", "inputSchema": {"type": "object"}}])
    recorded: list[str] = []

    async def record(type_: str, payload: dict[str, Any]) -> None:
        recorded.append(type_)

    proxy = McpProxy(
        _step(stage_input),
        record,
        GuardRails(stage_input.permissions, list(stage_input.allowed_paths), workspace="/workspace"),
        transport=server.transport(),
        verbatim=True,
    )
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ping", "arguments": {}}}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy.app()), base_url="http://127.0.0.1:4002"
    ) as agent:
        answer = await agent.post(
            "/mcp/crm", json=call, headers={"accept": "application/json", "mcp-session-id": server.session}
        )

    assert answer.status_code == 200
    [(_, headers, _)] = server.received
    assert headers["authorization"] == MCP_HEADER, "verbatim, as the step declared it"
    assert "session/request_permission" in recorded, "judged by the guard rails, whoever sends it"


# ───────────────────────── a whole run ─────────────────────────

#: Which of the probe's variables hold each secret, by digest: the probe carries none.
PROBE = (
    "import hashlib, os, sys\n"
    "for arg in sys.argv[1:]:\n"
    "    label, digest = arg.split('=', 1)\n"
    "    held = sorted(k for k, v in os.environ.items()\n"
    "                  if digest in hashlib.sha256(v.encode()).hexdigest()\n"
    "                  or any(hashlib.sha256(p.encode()).hexdigest() == digest\n"
    "                         for p in v.replace('@', ':').replace('/', ':').split(':')))\n"
    "    print(label + ' -> ' + ','.join(held))\n"
    "print('proxy -> ' + os.environ.get('HTTPS_PROXY', ''))\n"
    "print('key -> ' + os.environ.get('OPENAI_API_KEY', ''))\n"
    "print('base -> ' + os.environ.get('OPENAI_BASE_URL', ''))\n"
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def _run_in_a_container(
    stage_input: StageInput, runner_settings: Any, port: int, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, dict[str, str], Runner]:
    monkeypatch.setenv("HTTPS_PROXY", PROXY_URL)
    monkeypatch.setenv("https_proxy", PROXY_URL)
    settings = _settings(runner_settings, launcher_port=port, launcher_wait_seconds=10)
    labels = {"gateway": GATEWAY, "egress": EGRESS, "mcp": MCP_HEADER}
    command = [sys.executable, "-c", PROBE, *(f"{k}={_digest(v)}" for k, v in labels.items())]
    agent_script(
        {"turns": [{"runs": [command], "result": valid_result(summary="{grep}")}]},
        stage_input,
        settings.workspace,
    )
    client = FakeInternalClient(stage_input)
    runner = Runner(settings, client)  # type: ignore[arg-type]
    runner.loopback_ports = loopback_ports(avoid=[port_of(settings.tools_url)])
    runner.model_transport = httpx.MockTransport(Gateway())
    runner.mcp_transport = FakeMcpServer().transport()
    outcome = await runner.execute(stage_input, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.OK, outcome.detail
    return outcome, _read_probe(client.results[0].summary), runner


async def test_the_agents_process_holds_neither_the_key_nor_the_egress_token_nor_the_mcp_header(
    stage_input: StageInput,
    runner_settings: Any,
    serving: tuple[int, list[Any]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, asked = serving

    _, seen, runner = await _run_in_a_container(_step(stage_input), runner_settings, port, monkeypatch)

    assert (seen["gateway"], seen["egress"], seen["mcp"]) == ("", "", ""), seen
    assert seen["key"] == LOOPBACK_KEY
    assert seen["base"] == loopback_url(runner.loopback_ports[0])
    assert seen["proxy"] == loopback_url(runner.loopback_ports[2])
    # `asked` records every process this test's loop starts, the runner's own git (which clones and
    # pushes through the egress proxy) included: only what went to the launcher is the agent's.
    sent = [
        (argv, env) for argv, env in asked if any("fake_agent" in str(a) or "make" in str(a) for a in argv)
    ]
    assert len(sent) >= 2, "the agent and the checks were started through the launcher"
    for argv, env in sent:
        assert EGRESS not in json.dumps(env), argv
        assert GATEWAY not in json.dumps(env), argv


async def test_gemini_cli_gets_the_loopback_key_and_address_too(
    stage_input: StageInput,
    runner_settings: Any,
    serving: tuple[int, list[Any]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S65-04f remainder: Gemini's own variables carry the loopback key and address, nothing real."""
    port, _ = serving
    step = _step(stage_input, backend="gemini-cli")
    agent_script({"turns": [{"result": valid_result()}]}, step, runner_settings.workspace)

    monkeypatch.setenv("HTTPS_PROXY", PROXY_URL)
    settings = _settings(runner_settings, launcher_port=port, launcher_wait_seconds=10)
    client = FakeInternalClient(step)
    runner = Runner(settings, client)  # type: ignore[arg-type]
    runner.loopback_ports = loopback_ports(avoid=[port_of(settings.tools_url)])
    seen: list[dict[str, str]] = []
    import choregos_runner.runner as module

    real = module.AcpClient

    class _Spy(real):  # type: ignore[valid-type, misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            seen.append(dict(self.env))

    monkeypatch.setattr(module, "AcpClient", _Spy)
    await runner.execute(step, client)  # type: ignore[arg-type]

    [env] = seen
    assert env["GEMINI_API_KEY"] == LOOPBACK_KEY
    assert env["GOOGLE_GEMINI_BASE_URL"] == loopback_url(runner.loopback_ports[0])
    assert GATEWAY not in json.dumps(env)
    assert EGRESS not in json.dumps(env) and MCP_HEADER not in json.dumps(env)
