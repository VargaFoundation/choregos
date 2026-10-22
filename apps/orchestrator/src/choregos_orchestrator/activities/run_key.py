# SPDX-License-Identifier: Apache-2.0
"""A run's gateway key: reserved, minted for the run's Secret, released however the run ends (#523).

- `prepare_stage` decides the budget and reserves the key's cap (`reserve_key`): a `gateway_keys`
  row named `reserved:<run_id>`, counted by `open_reservations` like a key, so that no other run
  takes that money before the key exists.
- `start_run` mints the key (`mint_run_key`) and hands it to the executor, which puts it in the
  run's Secret next to the run token; the runner reads it from `CHOREGOS_RUN_GATEWAY_KEY`. The
  reservation row becomes the key's row. The key itself is never written to `runs.stage_input`
  and never travels in a Temporal payload.
- `release_run_key` revokes it, closes the reservation and scrubs any copy left on the row (a
  run prepared before #523). `collect_spend` calls it at the end of every run that gets there;
  `cancel_run` and a dying interpreter (`record_workflow_failure`) get there too.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from choregos_adapters.base import llm_guard_refusal
from choregos_adapters.errors import ConfigurationError
from choregos_api.db.models import GatewayKeyRow, Run
from choregos_api.logging import get_logger
from choregos_contracts import Budget, StageInput
from choregos_core import utcnow
from choregos_core.llm_guardrail import GUARD_METADATA_KEY, GUARD_REDACT
from sqlalchemy import select

from .base import without_secrets

logger = get_logger("choregos.run_key")

#: What a reservation's `key_id` starts with: no gateway knows it, nothing is revoked there.
RESERVED_PREFIX = "reserved:"


def reservation_id(run_id: str) -> str:
    return f"{RESERVED_PREFIX}{run_id}"


def key_ttl_seconds(max_minutes: int) -> int:
    """How long a run's key lives: its stage budget, plus 30 minutes."""
    return (int(max_minutes) + 30) * 60


async def _key_row(session: Any, key_id: str) -> GatewayKeyRow | None:
    row: GatewayKeyRow | None = (
        await session.execute(select(GatewayKeyRow).where(GatewayKeyRow.key_id == key_id))
    ).scalar_one_or_none()
    return row


async def reserve_key(session: Any, bundle: Any, run_id: str, budget: Budget) -> None:
    """Reserves the run's cap before its key exists. Replayable: the row is found by its id."""
    expires_at = utcnow() + timedelta(seconds=key_ttl_seconds(budget.max_minutes))
    row = await _key_row(session, reservation_id(run_id))
    if row is None:
        session.add(
            GatewayKeyRow(
                key_id=reservation_id(run_id),
                run_id=run_id,
                project_id=bundle.project.id,
                budget_usd=budget.usd,
                expires_at=expires_at,
            )
        )
        return
    row.run_id = run_id
    row.budget_usd = budget.usd
    row.expires_at = expires_at
    row.revoked = False


async def mint_key(
    bundle: Any, metadata: dict[str, Any], *, budget_usd: float, ttl_s: int, models: list[str]
) -> Any:
    """Every run key is minted here: at `start_run`, and afresh when a suspended run resumes.

    The project's day budget is also the gateway's, through a team every key of the project joins
    (S27-10): the reservation is the platform's read of it, the team is the hard stop. A resumed
    run's key joins it too, so the continuation counts against the project's day like any run.
    """
    if getattr(bundle.policy.sandbox, "llm_security_analyzer", False):
        # The gateway's guard masks secrets for this key only (ADR 0084). Key metadata is the
        # platform's: the agent holds the key, never the right to rewrite it.
        if (refusal := llm_guard_refusal(bundle.adapters.gateway)) is not None:
            raise ConfigurationError(refusal)
        metadata = {**metadata, GUARD_METADATA_KEY: GUARD_REDACT}
    team_id = None
    if (daily := bundle.engine.daily_budget()) is not None:
        from .provisioning import project_team_id

        team_id = await bundle.adapters.gateway.ensure_team(project_team_id(bundle.project.id), daily)
    return await bundle.adapters.gateway.mint_key(
        metadata, budget_usd=budget_usd, ttl_s=ttl_s, models=models, team_id=team_id
    )


async def mint_run_key(session: Any, bundle: Any, run: Run, stage_input: StageInput) -> str:
    """Mints the run's key, for the run's Secret only; the reservation row becomes its row.

    Called by `start_run` only when the run has not started (no executor ref): a retried
    `start_run` whose first attempt was rolled back mints again, and the executor rewrites the
    run's Secret with the new key — the key of the rolled-back attempt is known to no row and
    dies at its TTL.
    """
    budget = stage_input.budget
    key = await mint_key(
        bundle,
        {
            "project": bundle.slug,
            "work_item": stage_input.work_item.key,
            "run_id": run.id,
            "transition": stage_input.transition.id,
            "role": stage_input.transition.role,
            "attempt": stage_input.attempt,
            "backend": stage_input.agent.backend,
        },
        budget_usd=budget.usd,
        ttl_s=key_ttl_seconds(budget.max_minutes),
        models=[stage_input.model.litellm_model],
    )
    # The key id can be deterministic (the direct gateway's): a row may already carry it.
    row = await _key_row(session, key.key_id) or await _key_row(session, reservation_id(run.id))
    if row is None:
        session.add(
            GatewayKeyRow(
                key_id=key.key_id,
                run_id=run.id,
                project_id=bundle.project.id,
                budget_usd=budget.usd,
                expires_at=key.expires_at,
            )
        )
    else:
        row.key_id = key.key_id
        row.run_id = run.id
        row.budget_usd = budget.usd
        row.expires_at = key.expires_at
        row.revoked = False
    run.gateway_key_id = key.key_id
    return str(key.key)


async def revoke(bundle: Any, key_id: str) -> bool:
    """Revokes a key at the gateway; a failure is logged, not fatal (#519).

    A gateway that cannot revoke must not undo what the caller already did, nor kill the stage.
    The key still dies at its TTL (stage budget + 30 min), and its row stays `revoked = false`,
    which says the truth.
    """
    try:
        await bundle.adapters.gateway.revoke(key_id)
    except Exception as error:  # a cleanup, never a reason to fail the stage
        logger.warning("gateway key not revoked", key_id=key_id, error=str(error)[:300])
        return False
    return True


async def release_run_key(session: Any, bundle: Any, run: Run, *, spend_usd: float | None = None) -> None:
    """The run is over: its key is revoked, its reservation closed, no copy left on its row.

    Idempotent: a key already revoked is not revoked again. Never raises on a gateway error.
    """
    if run.gateway_key_id and not run.gateway_key_id.startswith(RESERVED_PREFIX):
        key_row = await _key_row(session, run.gateway_key_id)
        if key_row is None or not key_row.revoked:
            revoked = await revoke(bundle, run.gateway_key_id)
            if key_row is not None:
                key_row.revoked = revoked
        if key_row is not None and spend_usd is not None:
            key_row.spend_usd = spend_usd
    # A reservation whose key was never minted (the run never started) frees its cap now.
    reservation = await _key_row(session, reservation_id(run.id))
    if reservation is not None:
        reservation.revoked = True
    # A run prepared before #523 kept its key on the row: it leaves with the run.
    if run.stage_input and run.stage_input.get("gateway_key") is not None:
        run.stage_input = without_secrets(run.stage_input, keep_gateway_key=False)
