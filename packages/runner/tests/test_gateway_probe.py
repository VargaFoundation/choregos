"""An agent that drops the gateway's refusal no longer hides it (S65-35, finding #1742).

opencode turns a provider's error into a `None` stream and ends its turn with `'NoneType' object
has no attribute '__aiter__'`: the gateway's `budget_exceeded` never reached the run, which ended
`agent_silencieux` with "check its access to the model", and the workflow retried it every day.
After a turn that ends in an error naming no refusal, the runner asks the gateway itself, once.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from choregos_contracts import ModelRef, StageInput, StageStatus
from choregos_runner.exits import Exit
from choregos_runner.gateway_probe import PROBE_EVENT, ask_the_gateway
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient, agent_script

#: What opencode's turn ends with when its provider's call failed (#1742).
OPENCODE_ERROR = "Internal error: 'NoneType' object has no attribute '__aiter__'"
#: An embedded LiteLLM forwarding to an upstream gateway whose key is over its budget: the upstream's
#: 422 wrapped in LiteLLM's own error, its type left empty.
UPSTREAM_BUDGET = {
    "error": {
        "message": "litellm.BadRequestError: OpenAIException - Error code: 422 - {'error': {'message': "
        "'Budget has been exceeded! Current cost: 1.02, Max budget: 1.0', 'type': 'budget_exceeded', "
        "'param': None, 'code': '400'}}",
        "type": None,
        "param": None,
        "code": "422",
    }
}
ACCESS_DENIED = {
    "error": {
        "message": "key not allowed to access model. This key can only access models=['other']",
        "type": "key_model_access_denied",
        "param": "model",
        "code": "401",
    }
}


class Gateway:
    """A gateway that answers every model call the same way, and remembers what it was asked."""

    def __init__(self, status: int, body: dict[str, Any] | None = None, *, down: bool = False) -> None:
        self.status = status
        self.body = body or {"choices": [{"message": {"role": "assistant", "content": "p"}}]}
        self.down = down
        self.calls: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(self.status, json=self.body)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


async def _execute(
    stage_input: StageInput, settings: Any, script: dict[str, Any], gateway: Gateway
) -> tuple[Any, FakeInternalClient]:
    agent_script(script, stage_input, settings.workspace)
    client = FakeInternalClient(stage_input)
    runner = Runner(settings, client)  # type: ignore[arg-type]
    runner.model_transport = gateway.transport()
    outcome = await runner.execute(stage_input, client)  # type: ignore[arg-type]
    return outcome, client


def _probes(client: FakeInternalClient) -> list[dict[str, Any]]:
    return [event["payload"] for event in client.events if event["type"] == PROBE_EVENT]


async def test_a_budget_refusal_the_agent_dropped_ends_the_run_on_budget(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """The issue's case: the turn ends on opencode's `NoneType` error, the gateway's answer names the
    budget, and the run stops with `reason="budget"` — which the workflow never retries — instead of
    `agent_silencieux`, with nothing retried inside the run either."""
    gateway = Gateway(400, UPSTREAM_BUDGET)
    outcome, client = await _execute(
        stage_input, runner_settings, {"turns": [{"error": OPENCODE_ERROR}]}, gateway
    )

    assert outcome.exit_code is Exit.BUDGET_EXCEEDED, outcome.detail
    assert outcome.result is not None
    assert outcome.result.status is StageStatus.FAILED
    assert outcome.result.reason == "budget"
    assert "budget_exceeded" in outcome.result.summary
    assert "refused the run's key" in outcome.result.summary
    types = [event["type"] for event in client.events]
    assert "result.repair" not in types, "a refused key refuses the repair turn too"
    [call] = gateway.calls
    assert call.headers["authorization"] == f"Bearer {stage_input.gateway_key}", "asked with the run's key"
    assert call.url == httpx.URL(f"{stage_input.model.base_url}/chat/completions")
    body = json.loads(call.content)
    assert body["model"] == stage_input.model.litellm_model
    assert body["max_tokens"] == 1 and body["stream"] is False
    [probe] = _probes(client)
    assert probe["status"] == 400 and probe["budget"] is True


async def test_another_refusal_is_named_in_the_summary(stage_input: StageInput, runner_settings: Any) -> None:
    """A gateway that refuses the key for another reason: the run stays `agent_silencieux` (retried as
    before), but its summary names what the gateway said. The repair turns end in the same error, and
    the gateway is asked once."""
    gateway = Gateway(401, ACCESS_DENIED)
    outcome, client = await _execute(
        stage_input, runner_settings, {"turns": [{"error": OPENCODE_ERROR}]}, gateway
    )

    assert outcome.result is not None
    assert outcome.result.reason == "agent_silencieux"
    assert outcome.exit_code is Exit.INVALID_RESULT
    assert "the gateway answers the run's key 401: key_model_access_denied" in outcome.result.summary
    assert OPENCODE_ERROR in outcome.result.summary, "the agent's own error is still there"
    assert "result.repair" in [event["type"] for event in client.events], "the run went on as before"
    assert len(gateway.calls) == 1, "asked once per run, not once per turn"
    assert len(_probes(client)) == 1


async def test_a_gateway_that_serves_the_key_changes_nothing(
    stage_input: StageInput, runner_settings: Any
) -> None:
    gateway = Gateway(200)
    outcome, client = await _execute(
        stage_input, runner_settings, {"turns": [{"error": OPENCODE_ERROR}]}, gateway
    )

    assert outcome.result is not None
    assert outcome.result.reason == "agent_silencieux"
    assert "gateway" not in outcome.result.summary
    assert len(gateway.calls) == 1
    [probe] = _probes(client)
    assert probe == {"status": 200, "budget": False, "error": ""}


async def test_a_gateway_out_of_reach_is_said_and_does_not_fail_the_diagnosis(
    stage_input: StageInput, runner_settings: Any
) -> None:
    gateway = Gateway(0, down=True)
    outcome, _ = await _execute(stage_input, runner_settings, {"turns": [{"error": OPENCODE_ERROR}]}, gateway)

    assert outcome.result is not None
    assert outcome.result.reason == "agent_silencieux"
    assert "the gateway could not be reached: ConnectError" in outcome.result.summary


async def test_a_turn_that_did_not_end_in_an_error_asks_nothing(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """A silent turn that ended normally: the gateway is not asked, no model call is spent."""
    gateway = Gateway(400, UPSTREAM_BUDGET)
    outcome, client = await _execute(
        stage_input, runner_settings, {"turns": [{"stop_reason": "end_turn"}]}, gateway
    )
    assert outcome.result is not None and outcome.result.reason == "agent_silencieux"
    assert not gateway.calls and not _probes(client)


async def test_a_refusal_the_agent_relayed_asks_nothing(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """A refusal in the agent's error is read where it is (S27-01): no model call is spent on it."""
    gateway = Gateway(400, UPSTREAM_BUDGET)
    relayed = "litellm.BudgetExceededError: Budget has been exceeded! Current cost: 5.01, Max budget: 5.0"
    outcome, _ = await _execute(stage_input, runner_settings, {"turns": [{"error": relayed}]}, gateway)
    assert outcome.result is not None and outcome.result.reason == "budget"
    assert not gateway.calls


async def test_an_answer_that_is_not_json_is_read_as_text() -> None:
    model = ModelRef(litellm_model="platform/standard", base_url="http://gateway:4000/v1/")

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://gateway:4000/v1/chat/completions")
        return httpx.Response(429, text="budget_exceeded: team over budget\n")

    answer = await ask_the_gateway(model, None, transport=httpx.MockTransport(handle))
    assert answer.status == 429
    assert answer.budget_refusal == "budget_exceeded: team over budget"
