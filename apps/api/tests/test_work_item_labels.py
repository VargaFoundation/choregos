"""The work item keeps its labels, and carries them to the train (#279, S22-17).

They served the routing (ADR 0031), then were lost: `signal_train` boarded with `labels: []`,
and a work item labelled `hotfix` never took the express lane when it was the interpreter that
boarded it. They live in `documents.labels`: neither the contract nor the database schema changes.
"""

from __future__ import annotations

import json
from typing import Any

from httpx import AsyncClient

REPO = {"full_name": "varga/billing-api"}
WHO = {"login": "a"}


async def _post(client: AsyncClient, event: str, delivery: str, body: dict[str, Any]) -> None:
    response = await client.post(
        "/api/v1/webhooks/github",
        content=json.dumps(body).encode(),
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "content-type": "application/json",
        },
    )
    assert response.status_code == 202, response.text


async def _labeling(client: AsyncClient, delivery: str, action: str, label: str, current: list[str]) -> None:
    """What GitHub sends: the label added or removed, and the work item's CURRENT list."""
    issue = {"number": 7, "title": "Credit note outage", "labels": [{"name": n} for n in current]}
    body = {
        "action": action,
        "repository": REPO,
        "issue": issue,
        "label": {"name": label},
        "sender": WHO,
    }
    await _post(client, "issues", delivery, body)


async def _labels() -> list[str]:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        item = (
            await session.execute(select(WorkItem).where(WorkItem.tracker_key == "varga/billing-api#7"))
        ).scalar_one()
        return list((item.documents or {}).get("labels") or [])


async def test_the_work_item_keeps_the_labels_of_its_birth_and_follows_them(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    # Born from an opening (`tracker.item.created`): only the birth stores its labels.
    issue = {
        "number": 7,
        "title": "Credit note outage",
        "labels": [{"name": "agent-ready"}, {"name": "hotfix"}],
    }
    await _post(
        client, "issues", "e0", {"action": "opened", "repository": REPO, "issue": issue, "sender": WHO}
    )
    assert await _labels() == ["agent-ready", "hotfix"], "the labels of its birth"

    await _labeling(client, "e2", "unlabeled", "hotfix", ["agent-ready"])
    assert await _labels() == ["agent-ready"], "a removed label goes away"

    await _labeling(client, "e3", "labeled", "hotfix", ["agent-ready", "hotfix"])
    assert await _labels() == ["agent-ready", "hotfix"], "a label added after the birth"


async def test_the_webhook_boards_the_work_item_with_its_labels(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The PR does not carry `hotfix`; the work item does: it takes the express lane."""
    from choregos_api.temporal import get_temporal

    await _labeling(client, "e1", "labeled", "agent-ready", ["agent-ready", "hotfix"])
    pr = {
        "number": 107,
        "merged": True,
        "html_url": "https://github.com/varga/billing-api/pull/107",
        "head": {"ref": "choregos/7-credit-notes", "sha": "sha7"},
        "base": {"ref": "main"},
        "labels": [{"name": "backend"}],
    }
    await _post(
        client,
        "pull_request",
        "merge-7",
        {"action": "closed", "repository": REPO, "pull_request": pr, "sender": WHO},
    )
    (boarding,) = [
        payload
        for train, name, payload in get_temporal().signals  # type: ignore[attr-defined]
        if train == "train-billing-api-prod" and name == "merged"
    ]
    assert boarding["labels"] == ["backend", "agent-ready", "hotfix"]
