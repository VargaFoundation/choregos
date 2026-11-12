# ruff: noqa: F811 - a fixture imported from another module is redefined by the parameter that names it
"""The `teams` delivery of an assistant's task outputs and owner notices (S63-25, ADR 0137).

Each guard has its test: the manager seals the webhook URL (a credential: refused unless https, never
returned, never in the audit log, deleted with the channel); a task's artifact is posted as a card once, after
the organisation opened the host and not before (the refused card never leaves, and is retried); a channel
nobody configured is recorded; a failing Teams is retried; the owners of an upgrade are told in Teams as one
card and a failing Teams never fails the upgrade; a member of the audience cannot touch the channel.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import choregos_adapters
import pytest
from choregos_adapters.fakes.teams import FakeTeamsWebhook
from httpx import AsyncClient
from sqlalchemy import select

from .kits.assistant_install import ORG
from .test_assistant_tasks import (  # noqa: F401
    TASKS,
    _installation_id,
    _produce,
    _ready,
    _runs,
    _subscriber,
    as_admin,
    markets,
    offline,
)

HOST = "prod-12.westeurope.logic.azure.com"
URL = f"https://{HOST}/workflows/SECRETPATH123/triggers/manual/paths/invoke?sig=SECRETSIG"
TEAMS = f"{ORG}/assistants/investment-assistant/delivery/teams"


@pytest.fixture
def teams(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeTeamsWebhook]:
    fake = FakeTeamsWebhook()
    monkeypatch.setattr(choregos_adapters, "FAKE_TEAMS", fake)
    yield fake


def _enforce(monkeypatch: pytest.MonkeyPatch) -> None:
    """Egress enforced from now on (after the installation's own connectors were opened)."""
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "enforce")
    reset_settings_cache()


async def _deliver() -> int:
    from choregos_api.db.session import session_scope
    from choregos_api.services.task_delivery import deliver_pending

    async with session_scope(orgs="*") as session:
        return await deliver_pending(session)


async def _ledger() -> dict[str, Any]:
    ((item, _),) = await _runs()
    return dict(item.documents["task_delivery"]["brief@1"])


async def _produced_run(person: AsyncClient) -> None:
    run = (await person.post(f"{TASKS}/morning_brief/run")).json()
    await _produce(run["work_item_id"], brief="Hello")


async def test_the_manager_seals_the_url_and_nobody_reads_it_back(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, teams: FakeTeamsWebhook
) -> None:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from choregos_api.secret_store import get_secret_store

    await _ready(client)
    member = await _subscriber(client, app, "member@varga.dev")
    assert (await member.put(TEAMS, json={"webhook_url": URL})).status_code == 403
    assert (await member.get(TEAMS)).status_code == 403
    assert (await member.delete(TEAMS)).status_code == 403

    await as_admin(client)
    assert (await client.get(TEAMS)).json() == {"configured": False, "version": None, "updated_at": None}
    for bad in ("http://" + HOST + "/x", f"https://user:pw@{HOST}/x", "https://", "not a url at all"):
        refused = await client.put(TEAMS, json={"webhook_url": bad})
        assert refused.status_code == 422, (bad, refused.text)
        assert "pw" not in refused.text
    put = await client.put(TEAMS, json={"webhook_url": URL})
    assert put.status_code == 200, put.text
    assert put.json()["configured"] is True and put.json()["version"] == 1
    assert "SECRET" not in put.text and "SECRET" not in (await client.get(TEAMS)).text
    assert (await client.put(TEAMS, json={"webhook_url": URL})).json()["version"] == 2, "a rotation"

    installation = (await client.get(f"{ORG}/assistants/investment-assistant")).json()
    assert "SECRET" not in str(installation), "the installation shows a reference, not the URL"
    store = get_secret_store()
    async with session_scope(orgs="*") as session:
        from choregos_api.db.models import Organization

        organisation = (await session.execute(select(Organization))).scalar_one()
        name = "assistant/investment-assistant/delivery/teams"
        assert (await store.get_many(session, organisation.id, [name]))[name] == URL
        audit = (
            (await session.execute(select(AuditLog).where(AuditLog.action.like("assistant.delivery.teams%"))))
            .scalars()
            .all()
        )
        assert [a.action for a in audit] == ["assistant.delivery.teams.set"] * 2
        assert all("SECRET" not in str(a.payload) for a in audit), "the audit never holds the URL"

    assert (await client.delete(TEAMS)).status_code == 204
    assert (await client.get(TEAMS)).json()["configured"] is False
    async with session_scope(orgs="*") as session:
        assert await store.meta(session, organisation.id, [name]) == {}, (
            "the sealed URL is deleted with the channel"
        )


async def test_a_task_output_is_posted_as_a_card_once_and_only_where_the_organisation_allows(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,
    markets: Any,
    teams: FakeTeamsWebhook,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _ready(client)
    _enforce(monkeypatch)
    mine = await _subscriber(client, app, "mine@varga.dev")
    await as_admin(client)
    assert (await client.put(TEAMS, json={"webhook_url": URL})).status_code == 200
    await _produced_run(mine)

    # The host is not opened: the card never leaves, the reason is on the ledger, and it is retried.
    assert await _deliver() == 0
    refused = (await _ledger())["teams"]
    assert refused["state"] == "failed" and "egress refused" in refused["error"]
    assert "SECRET" not in refused["error"] and teams.posts == []

    opened = await client.post(f"{ORG}/egress/rules", json={"host": HOST})
    assert opened.status_code == 201, opened.text
    assert await _deliver() >= 1
    assert (await _ledger())["teams"]["state"] == "sent"
    ((url, _),) = teams.posts
    assert url == URL, "the card went to the sealed URL"
    assert teams.texts()[:2] == ["morning_brief: brief", "Hello"]
    assert {"task: morning_brief", "artifact: brief"} <= set(teams.texts())
    # Once per artifact revision: a second pass posts nothing.
    await _deliver()
    assert len(teams.posts) == 1


async def test_a_channel_nobody_configured_is_recorded_not_silent(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, teams: FakeTeamsWebhook
) -> None:
    await _ready(client)
    mine = await _subscriber(client, app, "mine@varga.dev")
    await _produced_run(mine)
    await _deliver()
    assert (await _ledger())["teams"]["state"] == "not_configured", "nothing sealed, nothing sent, said so"
    assert teams.posts == []


async def test_a_failing_teams_is_retried_and_then_sent_once(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, teams: FakeTeamsWebhook
) -> None:
    await _ready(client)
    mine = await _subscriber(client, app, "mine@varga.dev")
    await as_admin(client)
    assert (await client.put(TEAMS, json={"webhook_url": URL})).status_code == 200
    teams.fail_with(500)
    await _produced_run(mine)
    await _deliver()
    failed = (await _ledger())["teams"]
    assert failed["state"] == "failed" and failed["attempts"] == 1 and "500" in failed["error"]
    assert "SECRET" not in failed["error"]
    teams.fail_with(202)
    await _deliver()
    await _deliver()
    assert (await _ledger())["teams"]["state"] == "sent"
    assert len(teams.posts) == 2, "the failed post and the one that went"
    assert teams.texts()[-5:][:2] == ["morning_brief: brief", "Hello"]


async def test_the_owners_of_an_upgrade_are_told_in_teams_as_one_card_and_a_failure_never_fails_it(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any, teams: FakeTeamsWebhook
) -> None:
    from choregos_api.db.models import AssistantInstallation
    from choregos_api.db.session import session_scope
    from choregos_api.services.offered_knowledge import notify_owners

    await _ready(client)

    async def notice() -> int:
        async with session_scope(orgs="*") as session:
            row = (await session.execute(select(AssistantInstallation))).scalar_one()
            return await notify_owners(session, row, "1.2.0", ["a.md", "b.md"])

    assert await notice() == 0 and teams.posts == [], "no channel, no notice"
    await as_admin(client)
    assert (await client.put(TEAMS, json={"webhook_url": URL})).status_code == 200
    assert await notice() == 1
    ((_, posted),) = teams.posts
    texts = teams.texts()
    assert texts[0] == "investment-assistant: 2 knowledge update(s) to decide"
    assert "a.md" in texts[1] and "b.md" in texts[1] and "installation: investment-assistant" in texts
    assert posted["type"] == "message"
    teams.fail_with(500)
    assert await notice() == 0, "a failing channel is logged, the upgrade goes on"
