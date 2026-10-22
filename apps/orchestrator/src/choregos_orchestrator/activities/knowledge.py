# SPDX-License-Identifier: Apache-2.0
"""Syncing a knowledge source (S33-04, ADR 0051): the activity the `KnowledgeSync` loop runs.

Idempotent (ADR 0008): `sync_source` moves the source's cursor only once its changes are applied,
and applying them twice changes nothing twice. A source removed meanwhile answers `gone`, and the
loop stops.
"""

from __future__ import annotations

from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError


@activity.defn(name="sync_knowledge_source")
async def sync_knowledge_source(payload: dict[str, Any]) -> dict[str, Any]:
    """One pass: sync when the source's row says it is due (S39-19), and answer how long to sleep.

    `not_due` touches nothing: a worker restart or `continue_as_new` does not sync off-schedule.
    """
    from choregos_api.db.models import KnowledgeSource
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge import refresh
    from choregos_api.knowledge.sync import sync_source

    org, source_id = str(payload["org"]), str(payload["source_id"])
    async with session_scope(orgs=[org]) as session:
        source = await session.get(KnowledgeSource, source_id)
        if source is None:
            return {"status": "gone"}
        decision = refresh.decide_pass(source, refresh.utcnow())
    if not decision.sync:
        return {"status": "not_due", "interval_seconds": decision.sleep_s}

    async def schedule_next() -> int:
        # Written on success and on failure alike, so the console's "next sync" stays true.
        now = refresh.utcnow()
        async with session_scope(orgs=[org]) as session:
            current = await session.get(KnowledgeSource, source_id)
            if current is None:
                return refresh.MIN_SLEEP_S
            current.next_sync_at = refresh.next_sync_after(current, now)
            return refresh.sleep_until(current.next_sync_at, now)

    try:
        result = await sync_source(org, source_id)
    except Exception:
        await schedule_next()
        raise
    if result.get("status") == "gone":
        return result
    return {**result, "interval_seconds": await schedule_next()}


# ------------------------------------------------------------------ distillation in passes (S42-10c)
#
# One activity per step of `choregos_api.knowledge.distill`: each stores its result by digest, so a
# replayed or retried activity makes no second model call and publishes nothing twice (ADR 0008).


def _refused(error: Exception) -> ApplicationError:
    """A refusal is final: retrying a base that is too large or a budget that is spent changes nothing."""
    return ApplicationError(
        str(error), getattr(error, "code", "gateway"), type="DistillationError", non_retryable=True
    )


@activity.defn(name="plan_distillation")
async def plan_distillation(payload: dict[str, Any]) -> dict[str, Any]:
    from choregos_api.knowledge.distill import DistillationError, step_plan

    try:
        return await step_plan(
            str(payload["org"]),
            str(payload["base_id"]),
            list(payload.get("exclude") or []),
            payload.get("max_usd"),
        )
    except DistillationError as error:
        raise _refused(error) from error


@activity.defn(name="distill_part")
async def distill_part(payload: dict[str, Any]) -> dict[str, Any]:
    from choregos_api.knowledge.distill import DistillationError, step_part

    try:
        return await step_part(
            str(payload["org"]),
            str(payload["base_id"]),
            int(payload["index"]),
            str(payload["digest"]),
            list(payload.get("exclude") or []),
            float(payload.get("spent_usd") or 0.0),
        )
    except DistillationError as error:
        raise _refused(error) from error


@activity.defn(name="reduce_distillation")
async def reduce_distillation(payload: dict[str, Any]) -> dict[str, Any]:
    from choregos_api.knowledge.distill import DistillationError, step_reduce

    try:
        return await step_reduce(
            str(payload["org"]),
            str(payload["base_id"]),
            [str(d) for d in payload["digests"]],
            list(payload.get("exclude") or []),
            float(payload.get("spent_usd") or 0.0),
        )
    except DistillationError as error:
        raise _refused(error) from error


@activity.defn(name="publish_distillation")
async def publish_distillation(payload: dict[str, Any]) -> dict[str, Any]:
    from choregos_api.knowledge.distill import DistillationError, step_publish

    try:
        return await step_publish(
            str(payload["org"]),
            str(payload["base_id"]),
            str(payload["digest"]),
            str(payload["requested_by"]),
            list(payload.get("exclude") or []),
            float(payload.get("spent_usd") or 0.0),
            int(payload.get("parts") or 1),
        )
    except DistillationError as error:
        raise _refused(error) from error


@activity.defn(name="fail_distillation")
async def fail_distillation(payload: dict[str, Any]) -> dict[str, Any]:
    from choregos_api.knowledge.distill import step_fail

    return await step_fail(
        str(payload["org"]),
        str(payload["base_id"]),
        str(payload["code"]),
        str(payload["error"]),
        str(payload["requested_by"]),
        float(payload.get("spent_usd") or 0.0),
    )
