"""The policy's `scope.deny_paths` reaches the run and the guarantee (C2, D2C plan §4).

Before C2 it was read only when an agent asked for a wider scope: the run's `permissions` did not carry
it, and `scope_respected` passed a change under `.github/workflows/` as long as the work item's scope
covered it. The stage input now carries the denied paths, and the guarantee fails on them.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

REPOSITORY = "varga/billing-api"
#: Inside the fixture's work item scope (`src/orders/**`), and denied by the policy below.
GENERATED = "src/orders/generated/client.py"
DENY = [".github/workflows/**", "src/orders/generated/**"]


async def _deny(setup: Fixture, paths: list[str]) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, setup.project_id)
        assert row is not None
        policy = policy_model(row)
        policy.scope.deny_paths = list(paths)
        row.json_doc = policy.model_dump(mode="json", exclude_none=True)


def _plan(setup: Fixture) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": "t-implement",
        "role": "implement",
        "from_state": "ready",
        "to_state": "done",
        "actor": "dev",
        "attempt": 1,
    }


async def test_the_run_carries_the_policys_denied_paths(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _deny(setup, DENY)
    stage_input = (await prepare_stage(_plan(setup)))["stage_input"]
    assert stage_input["permissions"]["deny_paths"] == DENY
    assert stage_input["permissions"]["write_paths"] == ["src/orders/**", "tests/orders/**"]


async def test_scope_respected_fails_on_a_denied_path_inside_the_work_items_scope(setup: Fixture) -> None:
    from choregos_orchestrator.activities.gates import check_scope_violations, evaluate_gates

    setup.adapters.scm.set_diff(
        REPOSITORY, "main", "choregos/123", [("src/orders/total.py", 20, 4), (GENERATED, 400, 0)]
    )
    payload = {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "gates": [{"name": "scope_respected"}],
    }
    await _deny(setup, [])
    [allowed] = await evaluate_gates(payload)
    assert allowed["passed"] is True, "without the policy, the work item's scope covers the file"

    await _deny(setup, DENY)
    [refused] = await evaluate_gates(payload)
    assert refused["passed"] is False
    assert refused["annotations"] == [GENERATED] and "scope.deny_paths" in refused["detail"]
    assert await check_scope_violations(payload) == [GENERATED]
