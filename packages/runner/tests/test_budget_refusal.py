"""A gateway budget refusal ends the run with `reason="budget"` (S27-01, ADR 0047).

Before, the runner reported it as `agent_silencieux` or `invalid_result`, two reasons the workflow
retries — with a fresh key at the full stage budget.
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import StageInput, StageStatus
from choregos_runner.budget import gateway_budget_refusal
from choregos_runner.exits import Exit

from .conftest import valid_result
from .test_runner_flow import execute

#: What LiteLLM answers once a virtual key has spent its `max_budget`.
LITELLM_REFUSAL = (
    "litellm.BudgetExceededError: Budget has been exceeded! Current cost: 5.0012, Max budget: 5.0"
)


async def test_a_refused_prompt_ends_the_run_on_budget(stage_input: StageInput, runner_settings: Any) -> None:
    """The agent's turn ends on the gateway's error: the result says `budget`, and nothing is retried
    inside the run either — no DoD loop, no result repair."""
    outcome, client = await execute(stage_input, runner_settings, {"turns": [{"error": LITELLM_REFUSAL}]})

    assert outcome.exit_code is Exit.BUDGET_EXCEEDED, outcome.detail
    assert outcome.result is not None
    assert outcome.result.status is StageStatus.FAILED
    assert outcome.result.reason == "budget"
    assert "Budget has been exceeded" in outcome.result.summary
    assert client.results and client.results[0].reason == "budget", "the result is posted"
    types = [event["type"] for event in client.events]
    assert "result.repair" not in types, "a refused key refuses the repair turn too"
    assert "dod.retry" not in types
    [journal] = [e["payload"] for e in client.events if e["type"] == "run.result"]
    assert "refused the run's key" in journal["summary"], "the transcript says why the run stopped"


async def test_a_refusal_relayed_as_text_is_still_a_refusal(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """claude-code relays the provider's error as text: the agent "spoke", yet it is a refusal."""
    relayed = 'API Error: 400 {"error": {"type": "budget_exceeded", "message": "Max budget: 5.0"}}'
    outcome, _ = await execute(stage_input, runner_settings, {"turns": [{"messages": [relayed]}]})
    assert outcome.result is not None
    assert outcome.result.reason == "budget"
    assert outcome.exit_code is Exit.BUDGET_EXCEEDED


async def test_a_refusal_on_stderr_is_read(stage_input: StageInput, runner_settings: Any) -> None:
    outcome, _ = await execute(stage_input, runner_settings, {"turns": [{"stderr": [LITELLM_REFUSAL]}]})
    assert outcome.result is not None
    assert outcome.result.reason == "budget"


async def test_a_done_result_is_kept_even_after_a_refusal(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """The agent finished and wrote its result before the key ran dry: the work is done."""
    outcome, _ = await execute(
        stage_input,
        runner_settings,
        {"turns": [{"stderr": [LITELLM_REFUSAL], "result": valid_result()}]},
    )
    assert outcome.exit_code is Exit.OK
    assert outcome.result is not None and outcome.result.status is StageStatus.DONE


async def test_an_agent_talking_about_a_budget_is_not_refused(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """A business agent writing about an exceeded budget does not stop its own run on budget."""
    outcome, _ = await execute(
        stage_input,
        runner_settings,
        {"turns": [{"messages": ["The marketing budget was exceeded by 12 % in March."]}]},
    )
    assert outcome.result is not None
    assert outcome.result.reason != "budget"


def test_the_gateway_signatures() -> None:
    assert gateway_budget_refusal([LITELLM_REFUSAL])
    assert gateway_budget_refusal([], ['{"error": {"type": "budget_exceeded"}}'])
    assert gateway_budget_refusal(["ExceededBudget: team over budget"])
    assert gateway_budget_refusal(["The budget was exceeded in Q3"]) is None
    assert gateway_budget_refusal([""], []) is None
