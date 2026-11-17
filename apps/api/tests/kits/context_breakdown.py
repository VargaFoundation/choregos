"""A run of a backend with its journal already written (the context breakdown tests, S64-08, S64-08b)."""

from __future__ import annotations

from typing import Any

from choregos_core import llm_spans
from sqlalchemy import select


async def run_with_events(
    project: dict[str, Any], run_id: str, backend: str | None, events: list[tuple[str, dict[str, Any]]]
) -> None:
    """The run `run_id` of `backend`, on a work item of its own, with `events` as its seq 1, 2, ..."""
    from choregos_api.db.models import Run, RunEvent, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key=f"northwind/x#{run_id}", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id=run_id,
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="triage",
                status="running",
                backend=backend,
            )
        )
        for seq, (type_, payload) in enumerate(events, start=1):
            session.add(RunEvent(run_id=run_id, seq=seq, type=type_, payload=payload, ts=utcnow()))


MASTER_KEY = "-".join(["sk", "master", "c0ffee" * 3])


def set_master_key(monkeypatch: Any) -> None:
    """The gateway's master key the callback signs with, for the API under test."""
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_GATEWAY_MASTER_KEY", MASTER_KEY)
    reset_settings_cache()


def signed(run_id: str, key: str = MASTER_KEY) -> dict[str, str]:
    """The callback's own header, from the callback's own function."""
    return {"authorization": f"Bearer {llm_spans.context_signature(key, run_id)}"}


def gateway_breakdown(call: int, tokens: int, **extra: Any) -> dict[str, Any]:
    return {
        "call": call,
        "at": f"2026-11-11T10:00:{call:02d}+00:00",
        "model": "claude-sonnet",
        "prompt_tokens": tokens,
        "max_input_tokens": None,
        "source": "gateway",
        "sections": {"system": tokens // 10, "skills": tokens // 10, "backend": tokens - 2 * (tokens // 10)},
        **extra,
    }


async def journal(run_id: str) -> list[tuple[int, str, dict[str, Any]]]:
    from choregos_api.db.models import RunEvent
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        rows = await session.execute(
            select(RunEvent.seq, RunEvent.type, RunEvent.payload)
            .where(RunEvent.run_id == run_id)
            .order_by(RunEvent.seq)
        )
        return [(seq, type_, dict(payload)) for seq, type_, payload in rows.all()]
