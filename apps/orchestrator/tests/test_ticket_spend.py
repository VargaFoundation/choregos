"""The ticket's spend is read from the ledger, tool calls included, and survives a restart (S27-03).

Before, it lived in workflow memory, summed from model spend only: an interpreter started again
for the same ticket counted from zero, and tool calls (`kind=tool`) were never in it.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

import pytest
from choregos_contracts import StageInput, StageResult

from .conftest import Fixture, refine_result
from .test_budget_reservation import spend
from .test_interpreter import _wait_state, start

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


def spending_runner(gateway: Any) -> Any:
    """A run that spends at the gateway (about 0.45 $ on the standard model), then is done."""

    def run(stage_input: StageInput) -> StageResult:
        key_id = stage_input.gateway_key.removeprefix("sk-fake-")
        gateway.record_usage(
            key_id, tokens_in=100_000, tokens_out=10_000, model=gateway.keys[key_id].models[0]
        )
        return refine_result("spec")

    return run


async def test_a_restarted_interpreter_keeps_the_spent_amount(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """An earlier interpreter of this ticket spent 3 $ — 2 in models, 1 in tools. The new one starts
    from 3 $, and adds what its own stage costs."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await spend(setup, 2.0)
    await spend(setup, 1.0, kind="tool")
    gateway = setup.adapters.gateway
    setup.adapters.executor.handler = spending_runner(gateway)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        status = await handle.query("status")
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()

    stage_cost = sum(s.cost_usd for s in gateway.spends.values())
    assert stage_cost > 0
    assert status["cost_usd"] == pytest.approx(3.0 + stage_cost), status

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        target = HISTORIES / "wi-ticket-spend-S27-03.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")
