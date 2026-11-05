"""A project's tool-call cap, and the `tool` lines a run's calls left in the cost ledger."""

from __future__ import annotations

from typing import Any


async def cap_the_project(project_id: str, cap: int) -> None:
    """`budgets.tool_calls_per_run` set to `cap` in the project's latest policy."""
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        policy = (
            (
                await session.execute(
                    select(PolicyDef)
                    .where(PolicyDef.project_id == project_id)
                    .order_by(PolicyDef.version.desc())
                )
            )
            .scalars()
            .first()
        )
        assert policy is not None
        document = dict(policy.json_doc)
        document["budgets"] = {**(document.get("budgets") or {}), "tool_calls_per_run": cap}
        policy.json_doc = document


async def tool_lines(run_id: str) -> list[Any]:
    """The run's `tool` lines: booked calls, and places still held under the cap (`IN_FLIGHT`)."""
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        query = select(CostLedger).where(CostLedger.run_id == run_id, CostLedger.kind == "tool")
        return list((await session.execute(query)).scalars())
