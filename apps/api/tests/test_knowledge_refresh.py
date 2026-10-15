"""When a knowledge source syncs (S39-19): the decision is the row's, pure; the API sets it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from choregos_api.knowledge.refresh import (
    MANUAL_IDLE_S,
    MIN_SLEEP_S,
    check_cron,
    decide_pass,
    next_sync_after,
)
from httpx import AsyncClient

NOW = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
BASE = "/api/v1/orgs/varga/knowledge"


def row(**kw: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "refresh_mode": "interval",
        "interval_minutes": 60,
        "refresh_cron": None,
        "refresh_timezone": "UTC",
        "last_synced_at": NOW - timedelta(minutes=10),
        "next_sync_at": NOW + timedelta(minutes=50),
        "sync_requested_at": None,
    }
    return SimpleNamespace(**{**fields, **kw})


def test_a_source_never_synced_is_due() -> None:
    assert decide_pass(row(last_synced_at=None, next_sync_at=None), NOW).sync


def test_an_interval_not_yet_over_is_not_due_and_sleeps_until_it_is() -> None:
    decision = decide_pass(row(), NOW)
    assert not decision.sync and decision.sleep_s == 50 * 60
    assert decide_pass(row(next_sync_at=NOW - timedelta(seconds=1)), NOW).sync


def test_a_request_after_the_last_sync_is_due_even_in_manual_mode() -> None:
    asked = row(refresh_mode="manual", next_sync_at=None, sync_requested_at=NOW - timedelta(minutes=1))
    assert decide_pass(asked, NOW).sync
    answered = row(refresh_mode="manual", next_sync_at=None, sync_requested_at=NOW - timedelta(minutes=20))
    assert not decide_pass(answered, NOW).sync


def test_manual_without_a_request_idles_a_day() -> None:
    decision = decide_pass(row(refresh_mode="manual", next_sync_at=None), NOW)
    assert (decision.sync, decision.sleep_s) == (False, MANUAL_IDLE_S)


def test_a_cron_is_due_at_its_fire_minute() -> None:
    cron = row(refresh_mode="cron", refresh_cron="0 6 * * *", refresh_timezone="Europe/Paris")
    after_last = NOW - timedelta(hours=6)
    fire = next_sync_after(cron, after_last)
    assert fire == datetime(2026, 10, 16, 4, tzinfo=UTC) and fire is not None
    assert not decide_pass(row(**{**cron.__dict__, "next_sync_at": fire}), fire - timedelta(minutes=1)).sync
    assert decide_pass(row(**{**cron.__dict__, "next_sync_at": fire}), fire).sync


def test_the_sleep_never_goes_under_the_floor() -> None:
    soon = row(next_sync_at=NOW + timedelta(seconds=20))
    assert decide_pass(soon, NOW).sleep_s == MIN_SLEEP_S


def test_a_naive_database_moment_is_read_as_utc() -> None:
    naive = row(
        next_sync_at=(NOW + timedelta(minutes=50)).replace(tzinfo=None),
        last_synced_at=NOW.replace(tzinfo=None),
    )
    assert decide_pass(naive, NOW).sleep_s == 50 * 60


def test_cron_validation_says_why() -> None:
    with pytest.raises(ValueError, match="minimum is 5"):
        check_cron("*/2 * * * *", "UTC")
    with pytest.raises(ValueError, match="5 fields"):
        check_cron("0 6 * *", "UTC")
    check_cron("0 6 * * 1-5", "Europe/Paris")


async def _base_with_source(client: AsyncClient, **refresh: Any) -> dict[str, Any]:
    await client.post(BASE, json={"slug": "framework", "name": "Framework"})
    created = await client.post(
        f"{BASE}/framework/sources",
        json={"kind": "web", "name": "Docs", "config": {"url": "https://docs.example.com/"}, **refresh},
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def test_a_cron_source_is_created_with_its_schedule(client: AsyncClient, admin: str) -> None:
    source = await _base_with_source(
        client, refresh_mode="cron", refresh_cron="0  6 * * *", refresh_timezone="Europe/Paris"
    )
    assert (source["refresh_mode"], source["refresh_cron"], source["refresh_timezone"]) == (
        "cron",
        "0 6 * * *",
        "Europe/Paris",
    )
    assert source["next_sync_at"] is None, "due: the first pass syncs"


@pytest.mark.parametrize(
    ("refresh", "reason"),
    [
        ({"refresh_mode": "cron", "refresh_cron": "*/1 * * * *"}, "minimum is 5"),
        ({"refresh_mode": "cron", "refresh_cron": "0 6 * * *", "refresh_timezone": "Mars/Base"}, "time zone"),
        ({"refresh_mode": "cron"}, "refresh_cron"),
        ({"refresh_mode": "interval", "refresh_timezone": "Mars/Base"}, "time zone"),
        ({"refresh_mode": "cron", "refresh_cron": "0 6 * *"}, "5 fields"),
    ],
)
async def test_a_refresh_is_refused_with_its_reason(
    client: AsyncClient, admin: str, refresh: dict[str, Any], reason: str
) -> None:
    await client.post(BASE, json={"slug": "framework", "name": "Framework"})
    refused = await client.post(
        f"{BASE}/framework/sources",
        json={"kind": "web", "name": "Docs", "config": {"url": "https://docs.example.com/"}, **refresh},
    )
    assert refused.status_code == 422, refused.text
    assert reason in refused.json()["detail"]


async def test_editing_the_refresh_takes_effect_now(client: AsyncClient, admin: str, app: Any) -> None:
    from choregos_api.db.models import KnowledgeSource
    from choregos_api.db.session import session_scope
    from choregos_api.temporal import get_temporal

    source = await _base_with_source(client)
    async with session_scope() as session:
        row_ = await session.get(KnowledgeSource, source["id"])
        assert row_ is not None
        row_.last_synced_at = datetime.now(UTC) - timedelta(minutes=10)
    temporal: Any = get_temporal()
    loop = f"knowledge-sync-{source['id']}"

    manual = await client.patch(f"{BASE}/framework/sources/{source['id']}", json={"refresh_mode": "manual"})
    assert manual.status_code == 200, manual.text
    assert manual.json()["refresh_mode"] == "manual" and manual.json()["next_sync_at"] is None
    assert (loop, "sync_now", {}) in temporal.signals, "the sleeping loop is woken"

    every = await client.patch(
        f"{BASE}/framework/sources/{source['id']}", json={"refresh_mode": "interval", "interval_minutes": 30}
    )
    next_sync = datetime.fromisoformat(every.json()["next_sync_at"]).replace(tzinfo=UTC)
    assert timedelta(minutes=19) < next_sync - datetime.now(UTC) < timedelta(minutes=21)

    cron = await client.patch(
        f"{BASE}/framework/sources/{source['id']}",
        json={"refresh_mode": "cron", "refresh_cron": "*/2 * * * *"},
    )
    assert cron.status_code == 422 and "minimum is 5" in cron.json()["detail"]


async def test_sync_now_records_the_request_and_sync_all_signals_every_source(
    client: AsyncClient, admin: str, app: Any
) -> None:
    from choregos_api.temporal import get_temporal

    first = await _base_with_source(client)
    second = (
        await client.post(
            f"{BASE}/framework/sources",
            json={"kind": "git", "name": "Repo", "config": {"url": "https://git.example.com/p.git"}},
        )
    ).json()
    one = await client.post(f"{BASE}/framework/sources/{first['id']}/sync")
    assert one.status_code == 202 and one.json()["sync_requested_at"] is not None
    temporal: Any = get_temporal()
    temporal.signals.clear()
    everything = await client.post(f"{BASE}/framework/sync")
    assert everything.status_code == 202, everything.text
    for source in (first, second):
        assert (f"knowledge-sync-{source['id']}", "sync_now", {}) in temporal.signals
    sources = (await client.get(f"{BASE}/framework")).json()["sources"]
    assert all(s["sync_requested_at"] for s in sources)
