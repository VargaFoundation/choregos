"""A webhook event for a work item whose interpreter has ended is kept (ADR 0102).

Temporal answers NOT_FOUND to the signal: the delivery used to fail with a 500 and roll its dedupe row
back, so the same delivery was processed again and again.
"""

from __future__ import annotations

import json
from typing import Any

from httpx import AsyncClient

from . import test_plugin_tracker as plugin_tracker
from .test_plugin_tracker import HOOK, SECRET, _ready

kube = plugin_tracker.kube  # the fake plugin tracker's fixture, reused

REPO = {"full_name": "varga/billing-api"}


async def _issue(client: AsyncClient, delivery: str, action: str, labels: list[str]) -> Any:
    issue = {"number": 7, "title": "Credit notes fail", "labels": [{"name": n} for n in labels]}
    body: dict[str, Any] = {"action": action, "repository": REPO, "issue": issue, "sender": {"login": "a"}}
    if action == "labeled":
        body["label"] = {"name": labels[-1]}
    return await client.post(
        "/api/v1/webhooks/github",
        content=json.dumps(body).encode(),
        headers={"X-GitHub-Event": "issues", "X-GitHub-Delivery": delivery},
    )


async def test_an_event_for_an_ended_interpreter_is_kept(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import get_temporal, interpreter_id

    opened = await _issue(client, "e0", "opened", ["agent-ready"])
    assert opened.status_code == 202, opened.text
    get_temporal().gone.add(interpreter_id("billing-api", "varga/billing-api#7"))  # type: ignore[attr-defined]
    first = await _issue(client, "e1", "labeled", ["hotfix"])
    assert first.status_code == 202, first.text
    assert first.json()["duplicate"] is False
    again = await _issue(client, "e1", "labeled", ["hotfix"])
    assert again.status_code == 202 and again.json()["duplicate"] is True


async def test_a_plugin_tracker_event_for_an_ended_interpreter_is_kept(
    client: AsyncClient, admin: str, kube: Any
) -> None:
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.temporal import get_temporal, interpreter_id
    from sqlalchemy import select

    fake = await _ready(client, kube)
    fake.add("INC0000001", "VPN drops")
    async with session_scope() as session:
        project = (await session.execute(select(Project).where(Project.slug == "desked"))).scalar_one()
        session.add(WorkItem(project_id=project.id, tracker_key="INC0000001", title="VPN drops"))
    get_temporal().gone.add(interpreter_id("desked", "INC0000001"))  # type: ignore[attr-defined]
    body = fake.delivery("INC0000001")
    first = await client.post(HOOK, content=body, headers={"X-Choregos-Secret": SECRET})
    assert first.status_code == 202, first.text
    assert first.json() == {"accepted": True, "duplicate": False, "events": 1}
    again = await client.post(HOOK, content=body, headers={"X-Choregos-Secret": SECRET})
    assert again.status_code == 202 and again.json()["duplicate"] is True
