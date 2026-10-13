"""Replaying a work item whose budget is spent needs an explicit, audited top-up (S27-04, ADR 0047).

Before, "Replay the stage" re-opened a full retry budget — and with it, money nobody granted.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .test_migration_de_ticket import _signaux, _ticket


async def _spend_it_all(project: dict[str, Any], ticket: dict[str, Any]) -> float:
    """Books the whole ticket budget in the ledger; returns that budget."""
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from choregos_api.services import policy_engine
    from choregos_core import utcnow

    async with session_scope() as session:
        budget = (await policy_engine(session, project["id"])).budget_ticket(None)
        session.add(
            CostLedger(
                project_id=project["id"],
                work_item_id=ticket["id"],
                kind="model",
                cost_usd=budget,
                ts=utcnow(),
            )
        )
    return budget


async def _replay(client: AsyncClient, ticket: dict[str, Any], **body: Any) -> Any:
    return await client.post(
        f"/api/v1/work-items/{ticket['id']}/actions", json={"action": "rerun_stage", **body}
    )


async def test_replaying_an_exhausted_ticket_without_a_top_up_is_refused(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    ticket = await _ticket(client, project)
    await _spend_it_all(project, ticket)
    refused = await _replay(client, ticket)
    assert refused.status_code == 409, refused.text
    assert "top_up_usd" in refused.text
    assert not [s for s in _signaux() if s[1] == "control"], "nothing is passed on"


async def test_a_top_up_is_granted_recorded_and_passed_on(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import AuditLog, WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    ticket = await _ticket(client, project)
    await _spend_it_all(project, ticket)
    granted = await _replay(client, ticket, top_up_usd=5, reason="one more try on the totals")
    assert granted.status_code == 202, granted.text
    assert [s for s in _signaux() if s[1] == "control"], "the replay is passed on"
    again = await _replay(client, ticket, top_up_usd=2.5)
    assert again.status_code == 202, again.text

    async with session_scope() as session:
        item = await session.get(WorkItem, ticket["id"])
        assert item is not None and item.budget_top_up_usd == 7.5
        audits = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "workitem.budget_top_up")))
            .scalars()
            .all()
        )
    assert [a.payload["top_up_usd"] for a in audits] == [5, 2.5]
    assert all(a.actor_id for a in audits), "the audit says who granted it"


async def test_a_ticket_with_money_left_replays_without_a_top_up(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    ticket = await _ticket(client, project)
    assert (await _replay(client, ticket)).status_code == 202


async def test_a_top_up_counts_in_what_is_left(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services import policy_engine
    from choregos_api.services.budgets import ticket_left

    ticket = await _ticket(client, project)
    await _spend_it_all(project, ticket)
    await _replay(client, ticket, top_up_usd=3)
    async with session_scope() as session:
        item = await session.get(WorkItem, ticket["id"])
        left = await ticket_left(session, await policy_engine(session, project["id"]), item)
    assert left == 3
