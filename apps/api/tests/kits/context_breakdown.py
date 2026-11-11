"""A run of a backend with its journal already written (the context breakdown tests, S64-08, S64-08b)."""

from __future__ import annotations

from typing import Any


async def run_with_events(
    project: dict[str, Any], run_id: str, backend: str | None, events: list[tuple[str, dict[str, Any]]]
) -> None:
    """The run `run_id` of `backend`, on a work item of its own, with `events` as its seq 1, 2, ..."""
    from choregos_api.db.models import Run, RunEvent, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key=f"varga/x#{run_id}", title="T", state="ready")
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
