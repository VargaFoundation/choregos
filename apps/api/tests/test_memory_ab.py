"""Memory A/B report: it must pay off, and we refuse to conclude too fast (S10-06)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from httpx import AsyncClient


async def _project(client: AsyncClient, slug: str, *, memory: bool) -> str:
    payload = {
        "slug": slug,
        "name": slug,
        "config": {
            "slug": slug,
            "org": "varga",
            "repo": {"url": f"https://github.com/varga/{slug}.git", "default_branch": "main"},
        },
    }
    response = await client.post("/api/v1/orgs/varga/projects", json=payload)
    assert response.status_code == 201, response.text
    project_id = str(response.json()["id"])

    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, project_id)
        assert row is not None
        model = policy_model(row)
        model.memory.enabled = memory
        row.json_doc = model.model_dump(mode="json", exclude_none=True)
    return project_id


async def _tickets(project_id: str, *, count: int, attempts: int, cost: float) -> None:
    """`count` closed work items, each with its implementation in `attempts` attempt(s)."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        for index in range(count):
            item = WorkItem(
                project_id=project_id,
                tracker_key=f"varga/x#{index}",
                title=f"work item {index}",
                state="deployed_prod",
                closed_at=utcnow() - timedelta(days=3),
            )
            session.add(item)
            await session.flush()
            for attempt in range(1, attempts + 1):
                session.add(
                    Run(
                        work_item_id=item.id,
                        project_id=project_id,
                        stage_role="implement",
                        attempt=attempt,
                        status="succeeded",
                        cost_usd=cost / attempts,
                    )
                )


async def test_without_enough_work_items_the_report_refuses_to_conclude(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _tickets(project["id"], count=2, attempts=1, cost=1.0)
    report = (await client.get("/api/v1/orgs/varga/memory/ab-report")).json()
    assert report["verdict"] == "not enough data"
    assert "10 are needed on each side" in report["detail"]


async def test_memory_pays_off_when_it_improves_the_first_pass(client: AsyncClient, admin: str) -> None:
    with_memory = await _project(client, "with-memory", memory=True)
    without_memory = await _project(client, "without-memory", memory=False)
    await _tickets(with_memory, count=12, attempts=1, cost=1.0)
    await _tickets(without_memory, count=12, attempts=2, cost=2.0)

    report = (await client.get("/api/v1/orgs/varga/memory/ab-report")).json()

    assert report["with_memory"]["projects"] == ["with-memory"]
    assert report["without_memory"]["projects"] == ["without-memory"]
    assert report["with_memory"]["first_pass_merge_rate"] == 1.0
    assert report["without_memory"]["first_pass_merge_rate"] == 0.0
    assert report["verdict"] == "memory pays off"
    assert "first pass" in report["detail"]


async def test_memory_does_not_pay_off_when_it_costs_more_without_gain(
    client: AsyncClient, admin: str
) -> None:
    with_memory = await _project(client, "with-memory", memory=True)
    without_memory = await _project(client, "without-memory", memory=False)
    await _tickets(with_memory, count=12, attempts=1, cost=6.0)
    await _tickets(without_memory, count=12, attempts=1, cost=2.0)

    report = (await client.get("/api/v1/orgs/varga/memory/ab-report")).json()

    assert report["verdict"] == "memory does not pay off", report["detail"]
    assert report["with_memory"]["cost_per_ticket_usd"] == 6.0


async def test_an_unknown_organisation_claims_nothing(client: AsyncClient, admin: str) -> None:
    report = await client.get("/api/v1/orgs/unknown/memory/ab-report")
    assert report.status_code in {200, 403}
    if report.status_code == 200:
        assert report.json()["verdict"] == "organisation inconnue"  # lang: fr-ok (the service's own verdict)


async def test_the_window_is_adjustable(client: AsyncClient, admin: str) -> None:
    with_memory = await _project(client, "with-memory", memory=True)
    await _tickets(with_memory, count=3, attempts=1, cost=1.0)
    report = (await client.get("/api/v1/orgs/varga/memory/ab-report", params={"weeks": 1})).json()
    assert report["weeks"] == 1
    assert report["with_memory"]["tickets"] == 3, "three days is within the week"
