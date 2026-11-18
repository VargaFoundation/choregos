"""Escalation helpers: the human requests a work item raised."""

from __future__ import annotations

from typing import Any

from ..conftest import Fixture


async def requests_of(setup: Fixture) -> list[Any]:
    from sqlalchemy import select
    from tranibor_api.db.models import HumanRequest
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        rows = (
            await session.execute(select(HumanRequest).where(HumanRequest.work_item_id == setup.work_item_id))
        ).scalars()
        return list(rows)
