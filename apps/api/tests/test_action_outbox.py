"""The approved row is the outbox of `ActionWorkflow` (ADR 0085, S40-19): a workflow starts once the
approval is committed — never for a decision whose transaction fails —, and an approval whose start
was lost is started again by the relay, once.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from .conftest import login

EFFECTS = [{"effect": "test.noop", "with": {}}]


@pytest.fixture(autouse=True)
def noop_effect() -> Iterator[None]:
    from choregos_api import effets

    async def noop(_ctx: Any, _params: dict[str, Any]) -> dict[str, Any]:
        return {}

    effets.declarer_un_effet("test.noop", noop)
    yield
    effets.reinitialiser()


async def _proposed(client: AsyncClient, app: Any, pid: str, count: int = 1) -> list[str]:
    """Actions proposed by a developer: who proposes does not decide."""
    member = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "dev@varga.dev", "role": "developer"}
    )
    assert member.status_code in {200, 201}, member.text
    dev = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    ids: list[str] = []
    try:
        await login(dev, "dev@varga.dev")
        for n in range(count):
            body = {"kind": "test.noop", "title": f"Nothing {n}", "params": {}, "effects": EFFECTS}
            proposed = await dev.post(f"/api/v1/projects/{pid}/actions", json=body)
            assert proposed.status_code == 201, proposed.text
            ids.append(str(proposed.json()["id"]))
    finally:
        await dev.aclose()
    return ids


async def _status(action_id: str) -> str:
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        action = await session.get(Action, action_id)
        assert action is not None
        return action.status


async def test_an_approval_whose_commit_fails_starts_no_workflow(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    from choregos_api.temporal import get_temporal

    [action_id] = await _proposed(client, app, project["id"])

    def refuse(_session: Session) -> None:
        raise RuntimeError("the database refuses the commit")

    event.listen(Session, "before_commit", refuse)
    try:
        try:
            decided = await client.post(
                f"/api/v1/projects/{project['id']}/actions/{action_id}/decision", json={"decision": "approve"}
            )
            assert decided.status_code >= 500, decided.text
        except RuntimeError:
            pass  # the transport may hand the application's exception back as it is
    finally:
        event.remove(Session, "before_commit", refuse)
    assert await _status(action_id) == "pending_approval", "the approval was not kept"
    assert f"action-{action_id}" not in get_temporal().started, (  # type: ignore[attr-defined]
        "a workflow started inside the transaction would run an approval nobody kept"
    )


async def test_a_lost_start_is_relayed_once_and_only_for_approved_actions(
    client: AsyncClient, project: dict[str, Any], app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope
    from choregos_api.services.action_relay import start_approved_actions
    from choregos_api.temporal import FakeTemporal, get_temporal

    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    pid = project["id"]
    stranded, pending, running = await _proposed(client, app, pid, count=3)

    async def temporal_down(_workflow_id: str, _payload: dict[str, Any]) -> str:
        raise OSError("temporal: connection refused")

    with monkeypatch.context() as patch:
        patch.setattr(fake, "start_action", temporal_down)
        decided = await client.post(
            f"/api/v1/projects/{pid}/actions/{stranded}/decision", json={"decision": "approve"}
        )
    # Committed: the start that follows the commit failing does not take the approval back.
    assert decided.status_code == 200 and decided.json()["status"] == "approved", decided.text
    assert fake.started == {}, "Temporal was down: nothing started"
    async with session_scope(orgs="*") as session:
        row = await session.get(Action, running)
        assert row is not None
        row.status = "running"

    assert await start_approved_actions(grace=timedelta(hours=1)) == [], (
        "a fresh approval is left to its own start"
    )
    assert await start_approved_actions(grace=timedelta(0)) == [stranded]
    assert fake.started == {f"action-{stranded}": {"action_id": stranded}}
    assert pending not in str(fake.started) and running not in str(fake.started)
    # Every replica relays, and a relay pass that comes again starts nothing twice.
    await start_approved_actions(grace=timedelta(0))
    assert list(fake.started) == [f"action-{stranded}"]


async def test_what_waits_for_a_commit_dies_with_a_rollback(app: Any) -> None:
    """A transaction rolled back, then a new one committed on the same session: what the first one
    had registered never runs — it belonged to writes that were not kept."""
    from choregos_api.db.session import after_commit, run_after_commit, session_scope

    ran: list[str] = []

    async def mark(name: str) -> None:
        ran.append(name)

    async with session_scope(orgs="*") as session:
        await session.execute(text("SELECT 1"))  # what registers a start has written first
        after_commit(session, lambda: mark("rolled back"))
        await session.rollback()
        after_commit(session, lambda: mark("committed"))
    assert ran == ["committed"]
    await run_after_commit(session)
    assert ran == ["committed"], "once each"
