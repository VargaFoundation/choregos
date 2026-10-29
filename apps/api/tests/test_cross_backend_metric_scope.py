"""The cross-backend metric measures what the policy decides (S47-19).

The policy swaps the reviewer of a coding agent. The metric therefore counts the reviews by coding
agents only, and compares each one with the implementer of the time of that review.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from choregos_core import utcnow
from httpx import AsyncClient


async def _runs(project_id: str, key: str, runs: list[tuple[str, str | None, int]]) -> None:
    """A work item with `runs`: (stage role, backend, minutes after the first run), in the last day."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope

    first = utcnow() - timedelta(hours=2)
    async with session_scope() as session:
        item = WorkItem(project_id=project_id, tracker_key=key, title=key, state="verifying")
        session.add(item)
        await session.flush()
        for role, backend, minutes in runs:
            session.add(
                Run(
                    work_item_id=item.id,
                    project_id=project_id,
                    stage_role=role,
                    attempt=1,
                    backend=backend,
                    status="succeeded",
                    result={"status": "done"},
                    created_at=first + timedelta(minutes=minutes),
                )
            )


async def _report(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    response = await client.get(f"/api/v1/projects/{project['id']}/metrics/cross-backend")
    assert response.status_code == 200
    report: dict[str, Any] = response.json()
    return report


async def test_verify_and_remote_reviews_are_not_counted(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """A verify run and a remote reviewer are not what `review.cross_backend` swaps."""
    await _runs(
        project["id"],
        "varga/billing-api#1",
        [("implement", "claude-code", 0), ("review", "a2a", 1), ("verify", "codex", 2)],
    )

    report = await _report(client, project)

    assert report["other_backend"]["reviews"] == 0
    assert report["same_backend"]["reviews"] == 0
    assert report["other_backend"]["backends"] == []


async def test_a_fixed_runtime_review_is_not_counted(client: AsyncClient, project: dict[str, Any]) -> None:
    await _runs(
        project["id"],
        "varga/billing-api#2",
        [("implement", "claude-code", 0), ("review", "code", 1), ("review", "prompt", 2)],
    )

    report = await _report(client, project)

    assert report["other_backend"]["reviews"] == 0
    assert report["same_backend"]["reviews"] == 0


async def test_a_review_by_a_coding_agent_still_counts(client: AsyncClient, project: dict[str, Any]) -> None:
    """The scope is narrowed to coding agents, not emptied: the same item's coding-agent reviews count."""
    await _runs(
        project["id"],
        "varga/billing-api#3",
        [
            ("implement", "claude-code", 0),
            ("review", "a2a", 1),
            ("verify", "codex", 2),
            ("review", "codex", 3),
            ("review", "claude-code", 4),
        ],
    )

    report = await _report(client, project)

    assert report["other_backend"]["reviews"] == 1
    assert report["other_backend"]["backends"] == ["codex"]
    assert report["same_backend"]["reviews"] == 1
    assert report["same_backend"]["backends"] == ["claude-code"]


async def test_a_review_is_compared_with_the_implementer_of_its_time(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """codex reviewed code written by claude-code; the item was re-implemented by codex afterwards.

    Compared with the last implementer overall (codex), that review looked like a same-backend one.
    """
    await _runs(
        project["id"],
        "varga/billing-api#4",
        [("implement", "claude-code", 0), ("review", "codex", 1), ("implement", "codex", 2)],
    )

    report = await _report(client, project)

    assert report["other_backend"]["reviews"] == 1
    assert report["same_backend"]["reviews"] == 0
