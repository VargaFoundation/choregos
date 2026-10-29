"""What the workflow map says beyond its shape (S47-39): who a person step waits for, and where the
workflow's work items wait and what its steps cost.

The map showed `Who: product-owners · 24 h` with no hint that the group might hold nobody, and no
step said how often it ran, failed or what it cost.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient


async def _member(org: str, email: str, groups: list[str], role: str, project_id: str | None = None) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        org_id = (await session.execute(select(Organization.id).where(Organization.slug == org))).scalar_one()
        user = User(email=email, display_name=email.split("@", maxsplit=1)[0].title(), groups=groups)
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=org_id, project_id=project_id, role=role))


async def test_a_group_lists_who_carries_it_and_who_may_answer_here(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _member("varga", "lea@varga.dev", ["product-owners"], "developer", project["id"])
    await _member("varga", "max@varga.dev", ["choregos:varga:product-owners"], "viewer", project["id"])
    await _member("varga", "sam@varga.dev", ["choregos:other:product-owners"], "developer", project["id"])
    await _member("varga", "noa@varga.dev", ["architects"], "developer", project["id"])
    answer = await client.get(f"/api/v1/projects/{project['id']}/groups/product-owners/members")
    assert answer.status_code == 200, answer.text
    members = {m["email"]: m["can_decide"] for m in answer.json()["members"]}
    assert members == {"lea@varga.dev": True, "max@varga.dev": False}, "a viewer carries it but cannot answer"
    empty = (await client.get(f"/api/v1/projects/{project['id']}/groups/release-captains/members")).json()
    assert empty == {"group": "release-captains", "members": []}


async def test_the_activity_counts_open_items_by_state_and_runs_by_step(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import Run, WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    workflows = (await client.get(f"/api/v1/projects/{project['id']}/workflows")).json()
    name = workflows[0]["name"]
    now = datetime.now(UTC)
    async with session_scope() as session:
        definition = (
            (
                await session.execute(
                    select(WorkflowDef.id).where(
                        WorkflowDef.project_id == project["id"], WorkflowDef.name == name
                    )
                )
            )
            .scalars()
            .first()
        )
        items = [
            WorkItem(
                project_id=project["id"],
                tracker_key=f"B-{i}",
                title="T",
                state=state,
                workflow_def_id=definition,
            )
            for i, state in enumerate(["ready", "ready", "spec_review"])
        ]
        items.append(
            WorkItem(project_id=project["id"], tracker_key="B-9", title="T", state="done",
                     workflow_def_id=definition, closed_at=now)
        )  # fmt: skip
        session.add_all(items)
        await session.flush()

        def run(item: Any, status: str, usd: float, minutes: int, **kw: Any) -> Run:
            return Run(
                work_item_id=item.id,
                project_id=project["id"],
                transition_id="t-implement",
                stage_role="implement",
                status=status,
                cost_usd=usd,
                started_at=now - timedelta(minutes=minutes),
                ended_at=now,
                **kw,
            )

        session.add_all([run(items[0], "succeeded", 1.5, 10), run(items[1], "failed", 0.5, 30)])
    answer = await client.get(f"/api/v1/projects/{project['id']}/workflows/{name}/activity")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert {s["state"]: s["open_items"] for s in body["states"]} == {"ready": 2, "spec_review": 1}
    (step,) = body["steps"]
    assert (step["transition"], step["runs"], step["succeeded"], step["failed"]) == ("t-implement", 2, 1, 1)
    assert step["cost_usd"] == 2.0 and step["median_minutes"] == 20.0
    unknown = await client.get(f"/api/v1/projects/{project['id']}/workflows/nope/activity")
    assert unknown.status_code == 404


async def test_a_state_s_wait_starts_when_the_item_entered_it_not_when_it_was_filed(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The map said how many items wait in a state but not since when: the filing date of the oldest
    is not its wait. The last `state_changed` event into the state is."""
    from choregos_api.db.models import Event, WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    name = (await client.get(f"/api/v1/projects/{project['id']}/workflows")).json()[0]["name"]
    now = datetime.now(UTC)
    async with session_scope() as session:
        definition = (
            (await session.execute(select(WorkflowDef.id).where(WorkflowDef.project_id == project["id"])))
            .scalars()
            .first()
        )
        moved = WorkItem(project_id=project["id"], tracker_key="W-1", title="T", state="ready",
                         workflow_def_id=definition, created_at=now - timedelta(days=10))  # fmt: skip
        fresh = WorkItem(project_id=project["id"], tracker_key="W-2", title="T", state="inbox",
                         workflow_def_id=definition, created_at=now - timedelta(hours=5))  # fmt: skip
        session.add_all([moved, fresh])
        await session.flush()

        def change(to: str, ago: timedelta) -> Event:
            return Event(
                project_id=project["id"],
                work_item_id=moved.id,
                type="choregos.workitem.state_changed",
                payload={"from": "x", "to": to},
                ts=now - ago,
            )

        # Into `ready` 9 days ago, out, and back 2 days ago: the wait counts from the last entry.
        session.add_all([change("ready", timedelta(days=9)), change("spec", timedelta(days=8)),
                         change("ready", timedelta(days=2))])  # fmt: skip
    body = (await client.get(f"/api/v1/projects/{project['id']}/workflows/{name}/activity")).json()
    # SQLite hands back naive datetimes (UTC); PostgreSQL aware ones.
    since = {
        s["state"]: datetime.fromisoformat(s["waiting_since"]).replace(tzinfo=UTC) for s in body["states"]
    }
    assert abs(since["ready"] - (now - timedelta(days=2))) < timedelta(minutes=1)
    assert abs(since["inbox"] - (now - timedelta(hours=5))) < timedelta(minutes=1), "no event: its filing"
