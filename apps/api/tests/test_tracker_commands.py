"""A `/choregos ...` tracker comment is answered with a console link and audited, never signalled (ADR 0102).

A tracker login is not a Choregos principal: the decision verbs and the controls are done in the console.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from choregos_adapters.fakes.tracker import FakeTracker
from choregos_adapters.tracker.github_events import parse_command
from httpx import AsyncClient
from sqlalchemy import select

KEY = "varga/billing-api#7"
REPO = {"full_name": "varga/billing-api"}


@pytest.fixture
def tracker(monkeypatch: pytest.MonkeyPatch) -> FakeTracker:
    from choregos_api.services import tickets

    fake = FakeTracker("billing-api")
    seeded = fake.seed("Credit notes fail")
    fake.items[KEY] = fake.items.pop(seeded).model_copy(update={"key": KEY})
    real = tickets.tracker_du_projet

    async def with_the_fake(session: Any, project: Any) -> Any:
        row, _ = await real(session, project)
        return row, (fake if row is not None and row.type not in {"internal", "fake"} else None)

    monkeypatch.setattr(tickets, "tracker_du_projet", with_the_fake)
    return fake


async def _post(client: AsyncClient, event: str, delivery: str, body: dict[str, Any]) -> Any:
    body = {"sender": {"login": "mallory"}, **body}
    return await client.post(
        "/api/v1/webhooks/github",
        content=json.dumps(body).encode(),
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "content-type": "application/json",
        },
    )


async def _routed_item(client: AsyncClient, project: dict[str, Any]) -> None:
    put = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker",
        json={"type": "github-issues", "config": {"repo": "varga/billing-api"}},
    )
    assert put.status_code == 200, put.text
    issue = {"number": 7, "title": "Credit notes fail", "labels": [{"name": "agent-ready"}]}
    opened = await _post(client, "issues", "d0", {"action": "opened", "repository": REPO, "issue": issue})
    assert opened.status_code == 202, opened.text


async def _comment(client: AsyncClient, delivery: str, text: str) -> Any:
    return await _post(
        client,
        "issue_comment",
        delivery,
        {
            "action": "created",
            "repository": REPO,
            "issue": {"number": 7, "title": "Credit notes fail"},
            "comment": {"body": text, "user": {"login": "mallory"}},
        },
    )


def _signals() -> list[tuple[str, str, Any]]:
    from choregos_api.temporal import get_temporal

    return get_temporal().signals  # type: ignore[attr-defined,no-any-return]


async def _audit(verb: str) -> list[Any]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        rows = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "tracker.command.redirected")))
            .scalars()
            .all()
        )
    return [r for r in rows if r.payload.get("verb") == verb]


def _decisions() -> list[Any]:
    return [s for s in _signals() if s[1] == "inbound" and s[2].get("type") == "human.decision"]


async def test_a_decision_command_is_answered_with_the_console_and_never_signalled(
    client: AsyncClient, project: dict[str, Any], tracker: FakeTracker
) -> None:
    await _routed_item(client, project)
    answered = await _comment(client, "d1", "/choregos approve")
    assert answered.status_code == 202, answered.text
    assert not _decisions()
    assert not [s for s in _signals() if s[1] == "control"]
    assert any("/inbox" in c.body for c in tracker.items[KEY].comments)
    assert len(await _audit("approve")) == 1


async def test_a_control_command_is_answered_with_the_item_link(
    client: AsyncClient, project: dict[str, Any], tracker: FakeTracker
) -> None:
    await _routed_item(client, project)
    answered = await _comment(client, "d2", "/choregos pause")
    assert answered.status_code == 202, answered.text
    assert any("/p/billing-api/items/" in c.body for c in tracker.items[KEY].comments)
    assert not [s for s in _signals() if s[1] == "control"] and not _decisions()
    assert len(await _audit("pause")) == 1


async def test_the_reply_is_not_read_as_a_command(
    client: AsyncClient, project: dict[str, Any], tracker: FakeTracker
) -> None:
    await _routed_item(client, project)
    await _comment(client, "d3", "/choregos approve")
    await _comment(client, "d4", "/choregos stop")
    replies = [c.body for c in tracker.items[KEY].comments if "choregos:command" in c.body]
    assert len(replies) == 2
    for reply in replies:
        assert parse_command(reply) is None


async def test_a_broken_tracker_connector_does_not_fail_the_webhook(
    client: AsyncClient, project: dict[str, Any], tracker: FakeTracker, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.errors import upstream
    from choregos_api.services import tickets

    await _routed_item(client, project)

    async def broken(session: Any, project: Any) -> Any:
        raise upstream("github-issues", "x")

    monkeypatch.setattr(tickets, "tracker_du_projet", broken)
    first = await _comment(client, "d5", "/choregos reject no")
    assert first.status_code == 202, first.text
    assert first.json()["duplicate"] is False
    assert len(await _audit("reject")) == 1
    again = await _comment(client, "d5", "/choregos reject no")
    assert again.status_code == 202 and again.json()["duplicate"] is True
