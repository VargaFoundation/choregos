"""`GET /orgs/{org}/budget-alerts` pages and filters in SQL (ADR 0206), what the caller may read included."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from tranibor_core.domain import utcnow

from .kits.budget_alert_members import _as, _drop_organisation_role, _member

URL = "/api/v1/orgs/northwind/budget-alerts"


async def _seed(
    count: int, *, scope: str = "project", subject: str = "billing-api", same_instant: bool = False,
    status: str = "open", prefix: str = "a", newer: bool = False,
) -> None:  # fmt: skip
    from sqlalchemy import select
    from tranibor_api.db.models import BudgetAlert, Organization
    from tranibor_api.db.session import session_scope

    start = utcnow() + (timedelta(days=1) if newer else timedelta())
    async with session_scope() as session:
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "northwind"))
        ).scalar_one()
        for index in range(count):
            session.add(
                BudgetAlert(
                    org_id=org_id,
                    scope=scope,
                    subject=subject,
                    period="day",
                    period_key=f"{prefix}{index:04d}",
                    threshold=80,
                    status=status,
                    created_at=start if same_instant else start + timedelta(seconds=index),
                )
            )


async def _all_pages(client: AsyncClient, **params: Any) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        response = await client.get(URL, params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        seen += body["items"]
        if not body["meta"]["has_more"]:
            assert body["meta"]["next_cursor"] is None
            return seen
        cursor = body["meta"]["next_cursor"]


async def test_every_alert_is_reachable_once_page_after_page(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _seed(120)
    assert len((await client.get(URL, params={"limit": 50})).json()["items"]) == 50
    seen = await _all_pages(client, limit=50)
    assert len(seen) == 120 and len({a["id"] for a in seen}) == 120
    stamps = [a["created_at"] for a in seen]
    assert stamps == sorted(stamps, reverse=True), "newest first"


async def test_alerts_sharing_one_instant_are_neither_skipped_nor_repeated(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _seed(75, same_instant=True)
    seen = await _all_pages(client, limit=10)
    assert len(seen) == 75 and len({a["id"] for a in seen}) == 75


async def test_the_status_and_project_filters_run_in_the_query(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _seed(5, status="acknowledged", prefix="done")
    await _seed(7, prefix="mine")
    await _seed(4, subject="other-project", prefix="theirs")
    await _seed(3, scope="agent", subject="coordinator", prefix="agent")
    assert len(await _all_pages(client, limit=3)) == 14, "open is the default"
    assert len(await _all_pages(client, status="acknowledged", limit=2)) == 5
    assert len(await _all_pages(client, status="all", limit=4)) == 19
    mine = await _all_pages(client, project="billing-api", limit=3)
    assert len(mine) == 7 and {a["subject"] for a in mine} == {"billing-api"}
    assert await _all_pages(client, project="coordinator") == [], "an agent's alert is not a project's"


async def test_a_member_of_one_project_gets_full_pages_of_what_they_may_read(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """What the caller may not read is cut in SQL, before the page: 60 newer agent alerts must not
    leave their first page empty (red with a filter applied to the rows after the page was cut)."""
    await _seed(60, scope="agent", subject="coordinator", prefix="agent", newer=True)
    await _seed(60, subject="other-project", prefix="theirs", newer=True)
    await _seed(60, prefix="mine")
    await _member("hakim@northwind.dev", "developer", project["slug"])
    async with _as(client, "hakim@northwind.dev") as member:
        await _drop_organisation_role("hakim@northwind.dev")
        first = (await member.get(URL, params={"limit": 50})).json()
        assert len(first["items"]) == 50 and first["meta"]["has_more"] is True
        seen = await _all_pages(member, limit=50)
    assert len(seen) == 60 and {(a["scope"], a["subject"]) for a in seen} == {("project", "billing-api")}


async def test_a_cursor_it_did_not_write_is_refused(client: AsyncClient, admin: str) -> None:
    assert (await client.get(URL, params={"cursor": "not-a-cursor"})).status_code == 422
    assert (await client.get(URL, params={"limit": 0})).status_code == 422
    assert (await client.get(URL, params={"project": "x" * 65})).status_code == 422
