"""A human request waiting for a person, as the decision drafts and the pending list need it."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


async def _request(
    client: AsyncClient, pid: str, title: str, *, user: str, kind: str = "approval", **payload: Any
) -> tuple[str, str, str]:
    """A work item waiting for `user` (an administrator's reassignment names them, ADR 0153)."""
    from choregos_api.db.models import HumanRequest, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    created = await client.post(f"/api/v1/projects/{pid}/work-items", json={"title": title, "start": False})
    assert created.status_code == 201, created.text
    item_id = str(created.json()["id"])
    async with session_scope() as session:
        item = await session.get(WorkItem, item_id)
        assert item is not None
        row = HumanRequest(
            work_item_id=item_id,
            project_id=pid,
            kind=kind,
            payload={"question": f"{title}?", **payload},
            requested_at=utcnow(),
            assignee={"user": user, "group": None},
        )
        session.add(row)
        await session.flush()
        return item_id, item.tracker_key, row.id
