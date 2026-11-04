"""Pause, resume and stop are honoured from a signed GitHub comment by a linked numeric id (S47-14).

Never from a login, a Jira display name, an unsigned delivery or an unlinked id. A decision is never taken
from a comment (ADR 0102)."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from choregos_adapters.fakes.tracker import FakeTracker
from httpx import AsyncClient
from sqlalchemy import select

KEY = "varga/billing-api#7"
REPO = {"full_name": "varga/billing-api"}
SECRET = "s3cr3t"


@pytest.fixture
def tracker(monkeypatch: pytest.MonkeyPatch) -> FakeTracker:
    from choregos_api.services import tickets

    fake = FakeTracker("billing-api")
    seeded = fake.seed("Credit notes fail")
    fake.items[KEY] = fake.items.pop(seeded).model_copy(update={"key": KEY})
    real = tickets.project_tracker

    async def with_the_fake(session: Any, project: Any) -> Any:
        row, _ = await real(session, project)
        return row, (fake if row is not None and row.type not in {"internal", "fake"} else None)

    monkeypatch.setattr(tickets, "project_tracker", with_the_fake)
    return fake


@pytest.fixture
def signed(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import get_settings

    monkeypatch.setattr(get_settings(), "github_webhook_secret", SECRET)


async def _deliver(
    client: AsyncClient, event: str, delivery: str, payload: dict[str, Any], secret: str | None
) -> Any:
    body = json.dumps({"sender": {"login": "someone"}, **payload}).encode()
    headers = {"X-GitHub-Event": event, "X-GitHub-Delivery": delivery, "content-type": "application/json"}
    if secret:
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Hub-Signature-256"] = f"sha256={digest}"
    return await client.post("/api/v1/webhooks/github", content=body, headers=headers)


async def _routed_item(client: AsyncClient, project: dict[str, Any], secret: str | None = SECRET) -> None:
    put = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker",
        json={"type": "github-issues", "config": {"repo": "varga/billing-api"}},
    )
    assert put.status_code == 200, put.text
    issue = {"number": 7, "title": "Credit notes fail", "labels": [{"name": "agent-ready"}]}
    opened = await _deliver(
        client, "issues", "d0", {"action": "opened", "repository": REPO, "issue": issue}, secret
    )
    assert opened.status_code == 202, opened.text


async def _comment(
    client: AsyncClient, delivery: str, text: str, user: dict[str, Any], *, secret: str | None = SECRET
) -> Any:
    payload = {
        "action": "created",
        "repository": REPO,
        "issue": {"number": 7, "title": "Credit notes fail"},
        "comment": {"body": text, "user": user},
        "sender": user,
    }
    return await _deliver(client, "issue_comment", delivery, payload, secret)


async def _link(client: AsyncClient, email: str, role: str, external_id: str, login_name: str) -> str:
    added = await client.post("/api/v1/orgs/varga/members", json={"email": email, "role": role})
    assert added.status_code == 201, added.text
    user_id = str(added.json()["user_id"])
    linked = await client.put(
        f"/api/v1/orgs/varga/members/{user_id}/tracker-identities/github",
        json={"external_id": external_id, "login": login_name},
    )
    assert linked.status_code == 200, linked.text
    return user_id


def _controls() -> list[tuple[str, str, Any]]:
    from choregos_api.temporal import get_temporal

    return [s for s in get_temporal().signals if s[1] == "control"]  # type: ignore[attr-defined]


def _decisions() -> list[Any]:
    from choregos_api.temporal import get_temporal

    return [
        s
        for s in get_temporal().signals  # type: ignore[attr-defined]
        if s[1] == "inbound" and s[2].get("type") == "human.decision"
    ]


async def _audit(action: str) -> list[Any]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all()
    return list(rows)


def _replies(tracker: FakeTracker) -> list[str]:
    return [c.body for c in tracker.items[KEY].comments if "choregos:command" in c.body]


async def _paused() -> bool:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return bool((await session.execute(select(WorkItem.paused))).scalar_one())


async def test_a_linked_developer_pauses_from_a_signed_comment(
    client: AsyncClient, project: dict[str, Any], tracker: FakeTracker, signed: None
) -> None:
    await _routed_item(client, project)
    await _link(client, "dev@varga.dev", "developer", "1001", "devon")

    answered = await _comment(client, "c1", "/choregos pause", {"login": "devon", "id": 1001})
    assert answered.status_code == 202, answered.text

    controls = _controls()
    assert len(controls) == 1
    assert controls[0][2]["action"] == "pause" and controls[0][2]["by"] == "dev@varga.dev"
    assert await _paused() is True
    audited = await _audit("workitem.pause")
    assert len(audited) == 1 and audited[0].payload["channel"] == "tracker"
    assert any("Done" in reply and "dev@varga.dev" in reply for reply in _replies(tracker))
    assert not _decisions()


async def test_a_renamed_github_login_does_not_inherit_the_link(
    client: AsyncClient, project: dict[str, Any], tracker: FakeTracker, signed: None
) -> None:
    await _routed_item(client, project)
    await _link(client, "dev@varga.dev", "developer", "1001", "mallory")

    await _comment(client, "c2", "/choregos pause", {"login": "mallory", "id": 2002})

    assert not _controls()
    assert await _paused() is False
    assert any("link your GitHub account" in reply for reply in _replies(tracker))


async def test_an_unsigned_delivery_honours_nothing(
    client: AsyncClient, project: dict[str, Any], tracker: FakeTracker
) -> None:
    from choregos_api.config import get_settings

    assert not get_settings().github_webhook_secret
    await _routed_item(client, project, secret=None)
    await _link(client, "dev@varga.dev", "developer", "1001", "devon")

    answered = await _comment(client, "c3", "/choregos pause", {"login": "devon", "id": 1001}, secret=None)
    assert answered.status_code == 202, answered.text

    assert not _controls()
    assert await _paused() is False


async def test_a_jira_display_name_is_not_an_identity(
    client: AsyncClient,
    project: dict[str, Any],
    tracker: FakeTracker,
    signed: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_api.config import get_settings

    monkeypatch.setattr(get_settings(), "generic_webhook_secret", "generic")
    monkeypatch.setenv("CONNECTOR_JIRA_TEST_TOKEN", "t")
    put = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker",
        json={
            "type": "jira",
            "config": {
                "base_url": "https://acme.atlassian.net",
                "email": "bot@acme.test",
                "project_key": "BILL",
            },
            "secret_refs": {"api_token": "env:CONNECTOR_JIRA_TEST_TOKEN"},
        },
    )
    assert put.status_code == 200, put.text
    await _link(client, "dev@varga.dev", "developer", "1001", "devon")
    tracker.items["BILL-7"] = tracker.items[KEY].model_copy(update={"key": "BILL-7"})
    issue = {
        "key": "BILL-7",
        "self": "https://acme.atlassian.net/rest/api/3/issue/1",
        "fields": {"summary": "Refunds", "labels": ["agent-ready"]},
    }
    headers = {"X-Choregos-Secret": "generic"}
    born = await client.post(
        "/api/v1/webhooks/jira",
        json={
            "webhookEvent": "jira:issue_updated",
            "user": {"displayName": "devon"},
            "issue": issue,
            "changelog": {"items": [{"field": "labels", "fromString": "", "toString": "agent-ready"}]},
        },
        headers={**headers, "X-Atlassian-Webhook-Identifier": "j1"},
    )
    assert born.status_code == 202, born.text

    said = await client.post(
        "/api/v1/webhooks/jira",
        json={
            "webhookEvent": "comment_created",
            "issue": issue,
            "comment": {"body": "/choregos pause", "author": {"displayName": "devon", "accountId": "1001"}},
        },
        headers={**headers, "X-Atlassian-Webhook-Identifier": "j2"},
    )
    assert said.status_code == 202, said.text

    assert not _controls()
    assert await _paused() is False


async def test_an_unlinked_id_or_a_viewer_changes_nothing(
    client: AsyncClient, project: dict[str, Any], tracker: FakeTracker, signed: None
) -> None:
    await _routed_item(client, project)
    await _link(client, "viewer@varga.dev", "viewer", "3003", "viola")

    await _comment(client, "c4", "/choregos pause", {"login": "stranger", "id": 4004})
    assert not _controls()
    assert any("link your GitHub account" in reply for reply in _replies(tracker))

    await _comment(client, "c5", "/choregos pause", {"login": "viola", "id": 3003})
    assert not _controls()
    assert await _paused() is False
    assert any("at least the developer role" in reply for reply in _replies(tracker))
    assert not await _audit("workitem.pause")


async def test_a_decision_is_never_taken_from_a_comment(
    client: AsyncClient, project: dict[str, Any], tracker: FakeTracker, signed: None
) -> None:
    await _routed_item(client, project)
    await _link(client, "boss@varga.dev", "org_admin", "5005", "boss")

    await _comment(client, "c6", "/choregos approve", {"login": "boss", "id": 5005})

    assert not _decisions() and not _controls()
    assert any("/inbox" in reply for reply in _replies(tracker))


async def test_two_commands_in_one_delivery_keep_their_own_members(
    client: AsyncClient, project: dict[str, Any], tracker: FakeTracker, signed: None
) -> None:
    from choregos_api.db.models import AuditLog, User
    from choregos_api.db.session import session_scope
    from choregos_api.routers import webhooks
    from choregos_contracts import InboundEvent, InboundEventType

    await _routed_item(client, project)
    await _link(client, "alice@varga.dev", "developer", "1001", "alice")
    await _link(client, "bob@varga.dev", "developer", "1002", "bob")

    def command(delivery: str, verb: str, by_id: str) -> InboundEvent:
        return InboundEvent(
            type=InboundEventType.HUMAN_DECISION,
            source="github",
            delivery_id=delivery,
            project_slug="billing-api",
            work_item_key=KEY,
            payload={"verb": verb, "argument": "", "channel": "tracker", "by": "x", "by_id": by_id},
        )

    async with session_scope() as session:
        await webhooks._dispatch(session, [command("e1", "pause", "1001"), command("e2", "resume", "1002")])
        await session.flush()
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action.like("workitem.%")).order_by(AuditLog.ts)
                )
            )
            .scalars()
            .all()
        )
        emails = {u.id: u.email for u in (await session.execute(select(User))).scalars().all()}
    assert [(r.action, emails[r.actor_id]) for r in rows] == [
        ("workitem.pause", "alice@varga.dev"),
        ("workitem.resume", "bob@varga.dev"),
    ]
    assert [c[2]["by"] for c in _controls()] == ["alice@varga.dev", "bob@varga.dev"]


async def test_a_principal_for_a_webhook_leaves_the_session_unnarrowed(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api import deps
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope

    await _link(client, "alice@varga.dev", "developer", "1001", "alice")
    scopes: list[Any] = []
    named: list[str] = []

    async def limit(session: Any, orgs: Any) -> None:
        scopes.append(orgs)

    async def name(session: Any, user_id: str) -> None:
        named.append(user_id)

    monkeypatch.setattr(deps, "limit_to_organisations", limit)
    monkeypatch.setattr(deps, "name_the_user", name)
    async with session_scope() as session:
        user = (await session.execute(select(User).where(User.email == "alice@varga.dev"))).scalar_one()
        await deps.principal_for_user(session, user, narrow=False)
        assert scopes == [deps.ALL_ORGS] and not named, "no narrowing and no attribution to this member"
        await deps.principal_for_user(session, user)
        assert scopes[-1] == ["varga"] and named == [user.id]
