"""No secret outlives its run in `runs.stage_input`, nor travels in a Temporal payload (#519).

On the dev tenant, `runs.stage_input` of finished runs held the run's gateway key and its callback
token in clear: the row outlives the run indefinitely, and backups copy it. The run token now lives
in the run's Secret only (`start_run` mints it), and so does the gateway key (#523): `start_run` mints
it for the executor, the row never holds it, and `collect_spend` revokes it.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_core.redaction import REDACTED

from .conftest import Fixture

pytestmark = pytest.mark.asyncio


async def _prepare(setup: Fixture) -> dict[str, Any]:
    from choregos_orchestrator.activities import stage as activities

    return await activities.prepare_stage(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "transition_id": "t-implement",
            "role": "implement",
            "actor": "dev",
            "from_state": "ready",
            "to_state": "in_progress",
            "attempt": 1,
        }
    )


async def _stored(run_id: str) -> dict[str, Any]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None and run.stage_input is not None
        return dict(run.stage_input)


def _minted_key(setup: Fixture) -> str:
    gateway = setup.adapters.gateway
    assert len(gateway.keys) == 1, "one key minted"
    return str(next(iter(gateway.keys.values())).key)


async def _start(setup: Fixture, prepared: dict[str, Any]) -> dict[str, Any]:
    from choregos_orchestrator.activities import stage as activities

    return await activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )


async def test_no_run_token_is_stored_and_no_secret_is_in_the_temporal_payload(setup: Fixture) -> None:
    prepared = await _prepare(setup)
    assert not setup.adapters.gateway.keys, "the key is minted when the run starts (#523)"
    started = await _start(setup, prepared)
    key = _minted_key(setup)

    # What Temporal records: neither the key nor a token.
    assert prepared["stage_input"]["gateway_key"] is None
    assert prepared["stage_input"]["callbacks"]["run_token"] == REDACTED
    assert key not in str(started)
    # What the row keeps: neither (#523).
    stored = await _stored(prepared["run_id"])
    assert stored["callbacks"]["run_token"] == REDACTED
    assert stored["gateway_key"] is None and key not in str(stored)
    # What the executor gets: the key, for the run's Secret, outside the input.
    spec = setup.adapters.executor.started[-1]
    assert spec.gateway_key == key
    assert spec.stage_input is not None and spec.stage_input.gateway_key is None


async def test_start_run_gives_the_executor_a_valid_run_token(setup: Fixture) -> None:
    from choregos_api.security import verify_run_token

    prepared = await _prepare(setup)
    await _start(setup, prepared)
    spec = setup.adapters.executor.started[-1]
    assert spec.run_token != REDACTED
    assert verify_run_token(spec.run_token).run_id == prepared["run_id"]
    assert spec.run_token not in str(await _stored(prepared["run_id"]))


async def test_a_reused_preparation_carries_no_secret_either(setup: Fixture) -> None:
    first = await _prepare(setup)
    again = await _prepare(setup)  # an activity retried after its commit
    assert again["reused"] is True and again["run_id"] == first["run_id"]
    assert again["stage_input"]["gateway_key"] is None
    assert again["stage_input"]["callbacks"]["run_token"] == REDACTED


async def test_the_key_is_revoked_and_dropped_from_the_row_when_the_run_ends(setup: Fixture) -> None:
    from choregos_api.db.models import GatewayKeyRow
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities import stage as activities
    from sqlalchemy import select

    prepared = await _prepare(setup)
    await _start(setup, prepared)
    key = _minted_key(setup)
    key_id = next(iter(setup.adapters.gateway.keys))
    await activities.collect_spend({"project_id": setup.project_id, "run_id": prepared["run_id"]})

    assert setup.adapters.gateway.revoked == [key_id]
    stored = await _stored(prepared["run_id"])
    assert stored["gateway_key"] is None
    assert key not in str(stored)
    async with session_scope() as session:
        row = (
            await session.execute(select(GatewayKeyRow).where(GatewayKeyRow.key_id == key_id))
        ).scalar_one()
        assert row.revoked is True


async def test_a_gateway_that_cannot_revoke_does_not_fail_the_stage(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import GatewayKeyRow
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities import stage as activities
    from sqlalchemy import select

    async def unreachable(key_id: str) -> None:
        raise ConnectionError("gateway unreachable")

    prepared = await _prepare(setup)
    await _start(setup, prepared)
    key_id = next(iter(setup.adapters.gateway.keys))
    monkeypatch.setattr(setup.adapters.gateway, "revoke", unreachable)
    spend = await activities.collect_spend({"project_id": setup.project_id, "run_id": prepared["run_id"]})

    assert spend["runs"] == 1
    assert (await _stored(prepared["run_id"]))["gateway_key"] is None
    async with session_scope() as session:
        row = (
            await session.execute(select(GatewayKeyRow).where(GatewayKeyRow.key_id == key_id))
        ).scalar_one()
        assert row.revoked is False, "the row says the truth: the key dies at its TTL"
