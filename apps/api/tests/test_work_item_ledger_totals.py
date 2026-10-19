"""A work item's cost totals are the ledger's, tools included (S27-03).

The counter kept on the row adds model spend only; the console shows `totals.cost_usd`.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .test_migration_de_ticket import _ticket


async def test_totals_add_the_tool_lines_of_the_ledger(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    ticket = await _ticket(client, project)
    before = await client.get(f"/api/v1/work-items/{ticket['id']}")
    assert before.json()["totals"]["cost_usd"] == 0
    async with session_scope() as session:
        for kind, usd in (("model", 1.25), ("tool", 0.5)):
            session.add(
                CostLedger(
                    project_id=project["id"], work_item_id=ticket["id"], kind=kind, cost_usd=usd, ts=utcnow()
                )
            )
    shown = await client.get(f"/api/v1/work-items/{ticket['id']}")
    assert shown.json()["totals"]["cost_usd"] == 1.75
