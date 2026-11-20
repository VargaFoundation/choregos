"""The ledger and the record of a distillation, read and set by the tests of its passes, ops and batch."""

from __future__ import annotations

from typing import Any


async def _ledger() -> list[Any]:
    from sqlalchemy import select
    from tranibor_api.db.models import CostLedger
    from tranibor_api.db.session import ALL_ORGS, session_scope

    async with session_scope(orgs=ALL_ORGS) as session:
        rows = (await session.execute(select(CostLedger).order_by(CostLedger.ts))).scalars().all()
        session.expunge_all()
        return list(rows)


async def _set_record(record: dict[str, Any] | None, **columns: Any) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import KnowledgeBase
    from tranibor_api.db.session import ALL_ORGS, session_scope

    async with session_scope(orgs=ALL_ORGS) as session:
        base = (await session.execute(select(KnowledgeBase))).scalars().one()
        base.distillation = record
        for name, value in columns.items():
            setattr(base, name, value)
