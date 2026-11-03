"""Archiving a project (the console's Archive, `DELETE /projects/{id}`) works whatever its status.

Only an `active` project used to be archived: a draft, a project left provisioning or a suspended one
answered 204, the audit said `project.archive`, and nothing changed. On 2026-11-02 a draft holding
the same Jira key as its successor could not be retired from the console.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient


async def _project(client: AsyncClient, slug: str) -> dict[str, Any]:
    created = await client.post(
        "/api/v1/orgs/varga/projects",
        json={"slug": slug, "name": slug, "config": {"slug": slug, "org": "varga"}},
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _archives(project_id: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(AuditLog.payload).where(
                    AuditLog.action == "project.archive", AuditLog.target_id == project_id
                )
            )
        ).scalars()
        return [dict(payload or {}) for payload in rows]


async def _status(client: AsyncClient, project_id: str) -> str:
    return str((await client.get(f"/api/v1/projects/{project_id}")).json()["status"])


@pytest.mark.parametrize("suspended", [False, True])
async def test_a_project_that_is_not_active_is_archived_too(
    client: AsyncClient, admin: str, suspended: bool
) -> None:
    project = await _project(client, "retired")
    assert project["status"] == "draft"
    if suspended:
        assert (await client.post(f"/api/v1/projects/{project['id']}/suspend")).status_code == 200

    assert (await client.delete(f"/api/v1/projects/{project['id']}")).status_code == 204

    assert await _status(client, project["id"]) == "archived"
    previous = "suspended" if suspended else "draft"
    assert await _archives(project["id"]) == [{"previous_status": previous}]


async def test_archiving_twice_changes_nothing_and_records_nothing_more(
    client: AsyncClient, admin: str
) -> None:
    project = await _project(client, "twice")
    assert (await client.delete(f"/api/v1/projects/{project['id']}")).status_code == 204
    assert (await client.delete(f"/api/v1/projects/{project['id']}")).status_code == 204

    assert await _status(client, project["id"]) == "archived"
    assert len(await _archives(project["id"])) == 1, "the audit says what happened, once"


async def test_archiving_stops_the_tracker_catch_up_loop(client: AsyncClient, admin: str) -> None:
    from choregos_api.temporal import get_temporal

    project = await _project(client, "polled")
    assert (await client.post(f"/api/v1/projects/{project['id']}/provision", json={})).status_code == 202
    assert "reconcile-polled" in get_temporal().started, "activation starts the project's catch-up loop"

    assert (await client.delete(f"/api/v1/projects/{project['id']}")).status_code == 204

    assert ("reconcile-polled", "stop", {}) in get_temporal().signals


async def test_a_loop_that_does_not_run_does_not_stop_the_archive(client: AsyncClient, admin: str) -> None:
    from choregos_api.temporal import get_temporal

    project = await _project(client, "never-polled")
    get_temporal().gone.add("reconcile-never-polled")  # Temporal answers NOT_FOUND to the signal

    assert (await client.delete(f"/api/v1/projects/{project['id']}")).status_code == 204
    assert await _status(client, project["id"]) == "archived"


async def test_with_the_catch_up_disabled_activation_starts_no_loop(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import get_settings
    from choregos_api.temporal import get_temporal

    monkeypatch.setattr(get_settings(), "reconcile_interval_seconds", 0)
    project = await _project(client, "quiet")

    assert (await client.post(f"/api/v1/projects/{project['id']}/provision", json={})).status_code == 202

    assert await _status(client, project["id"]) == "active"
    assert "reconcile-quiet" not in get_temporal().started


# ── the memory loop (S51-02) ──


async def _disable_memory(project_id: str) -> None:
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(
                select(PolicyDef).where(PolicyDef.project_id == project_id, PolicyDef.is_active.is_(True))
            )
        ).scalar_one()
        row.json_doc = {
            **row.json_doc,
            "memory": {**dict(row.json_doc.get("memory") or {}), "enabled": False},
        }


async def test_activation_starts_the_memory_loop_and_archiving_stops_it(
    client: AsyncClient, admin: str
) -> None:
    """Nothing started `MemoryIngestion`: the memory was never fed, and `mem-<slug>` signals went nowhere."""
    from choregos_api.temporal import get_temporal

    project = await _project(client, "remembers")
    assert (await client.post(f"/api/v1/projects/{project['id']}/provision", json={})).status_code == 202
    assert get_temporal().started["mem-remembers"] == {"project_slug": "remembers"}

    assert (await client.delete(f"/api/v1/projects/{project['id']}")).status_code == 204
    assert ("mem-remembers", "stop", {}) in get_temporal().signals


async def test_a_policy_without_memory_starts_no_memory_loop(client: AsyncClient, admin: str) -> None:
    from choregos_api.temporal import get_temporal

    project = await _project(client, "forgets")
    await _disable_memory(project["id"])
    assert (await client.post(f"/api/v1/projects/{project['id']}/provision", json={})).status_code == 202

    assert await _status(client, project["id"]) == "active"
    assert "reconcile-forgets" in get_temporal().started
    assert "mem-forgets" not in get_temporal().started


async def test_a_memory_loop_that_does_not_run_does_not_stop_the_archive(
    client: AsyncClient, admin: str
) -> None:
    from choregos_api.temporal import get_temporal

    project = await _project(client, "never-remembered")
    get_temporal().gone.add("mem-never-remembered")  # Temporal answers NOT_FOUND to the signal

    assert (await client.delete(f"/api/v1/projects/{project['id']}")).status_code == 204
    assert await _status(client, project["id"]) == "archived"
