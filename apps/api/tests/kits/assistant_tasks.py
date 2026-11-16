# SPDX-License-Identifier: Apache-2.0
"""Helpers shared by `test_assistant_tasks` and its neighbours (tests import kits, not each other)."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from ..conftest import login
from ..test_agent_sessions import Upstream, upstream  # noqa: F401
from .assistant_install import (
    FIXTURE,
    MARKET,
    NEWS,
    ORG,
    _bind,
    _connector,
    _install,
    _member,
    _plugin,
)

AUDIENCE = "investors"


ME = "/api/v1/me"


TASKS = f"{ME}/assistants/varga/investment-assistant/tasks"


WORKFLOW = """apiVersion: choregos/v1
kind: Workflow
metadata: {name: morning-brief, version: 1}
actors:
  me: {type: human, group: investors}
states:
  brief: {display: Brief, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - {id: t-done, from: brief, to: done, by: me}
"""


TASK_BLOCK = """tasks:
  - name: morning_brief
    workflow: workflows/morning-brief.yaml
    triggers: [{ kind: on_demand }, { kind: schedule, cron: "30 7 * * 1-5", user_timezone: true }]
    delegation: none
    needs: [news]
    deliver: [inbox, email, webhook, teams]
  - name: delegated_brief
    workflow: workflows/morning-brief.yaml
    triggers: [{ kind: schedule, cron: "30 7 * * 1-5", user_timezone: true }, { kind: on_demand }]
    delegation: read_only
    needs: [brokerage]
    deliver: [inbox]
  - name: threshold_alert
    workflow: workflows/morning-brief.yaml
    triggers:
      - kind: poll
        cron: "*/15 9-17 * * 1-5"
        operation: market_data__get_quote
        when: { /events/*/change_pct: { abs_gte: 3 } }
        idempotency: /events/*/id
    delegation: none
    deliver: [inbox]
  - name: meeting_prep
    workflow: workflows/morning-brief.yaml
    triggers: [{ kind: trigger, subscriber: /attendee/email, idempotency: /event/id }]
    delegation: none
    needs: [news]
    deliver: [inbox]
  - name: delegated_event
    workflow: workflows/morning-brief.yaml
    triggers: [{ kind: trigger, subscriber: /who/* }]
    delegation: read_only
    needs: [brokerage]
    deliver: [inbox]
"""


MONDAY = (2030, 1, 7)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(*MONDAY, hour, minute, tzinfo=UTC)


@pytest.fixture
def offline(tmp_path: Path) -> Iterator[Path]:
    from choregos_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    root = tmp_path / "plugins"
    target = root / "investment-assistant" / "0.1.0"
    shutil.copytree(FIXTURE, target)
    manifest = target / "assistants" / "investment-assistant.yaml"
    text = manifest.read_text(encoding="utf-8")
    start, end = text.index("tasks:\n"), text.index("surfaces:\n")
    manifest.write_text(text[:start] + TASK_BLOCK + text[end:], encoding="utf-8")
    (target / "workflows" / "morning-brief.yaml").write_text(WORKFLOW, encoding="utf-8")
    use_plugin_supply(OfflineDirectory(root))
    yield root
    use_plugin_supply(None)


@pytest.fixture
def markets(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    tools = [
        {
            "name": n,
            "description": n,
            "annotations": {"readOnlyHint": True},
            "inputSchema": {"type": "object"},
        }
        for n in MARKET
    ]
    server = FakeMcpServer(tools=tools, token="markets-key")
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)
    monkeypatch.setenv("CONNECTOR_TEST_MARKETS_KEY", "markets-key")
    yield server


async def _ready(client: AsyncClient) -> None:
    await _plugin(client)
    await _connector("news-feed", NEWS)
    installed = await _install(client, audience_groups=[AUDIENCE])
    assert installed.status_code == 201, installed.text
    await _bind(client, "news", "news-feed")
    body = {
        "name": "markets",
        "type": "mcp",
        "config": {"url": "https://markets.test/mcp"},
        "secret_refs": {"token": "env:CONNECTOR_TEST_MARKETS_KEY"},
    }
    assert (await client.post(f"{ORG}/connectors", json=body)).status_code == 201
    assert (await client.post(f"{ORG}/connectors/markets/discover")).status_code == 200
    for operation in MARKET:
        opened = await client.patch(
            f"{ORG}/connectors/markets/operations/{operation}", json={"policy": "allowed"}
        )
        assert opened.status_code == 200, opened.text
    ready = await _bind(client, "market_data", "markets")
    assert ready.json()["status"] == "ready", ready.text


async def _as(app: Any, email: str) -> AsyncClient:
    person = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(person, email)
    return person


async def _subscriber(client: AsyncClient, app: Any, email: str) -> AsyncClient:
    await _member(client, email, [AUDIENCE])
    return await _as(app, email)


async def _runs(email: str | None = None) -> list[Any]:
    """The task work items, with the owner of the personal space they are in."""
    from choregos_api.db.models import Project, User, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        query = (
            select(WorkItem, User.email)
            .join(Project, Project.id == WorkItem.project_id)
            .join(User, User.id == Project.owner_user_id)
            .where(Project.kind == "personal")
            .order_by(WorkItem.tracker_key)
        )
        rows = (await session.execute(query)).all()
        found = [(item, owner) for item, owner in rows if (item.documents or {}).get("task")]
        for item, _ in found:
            session.expunge(item)
        return [(i, o) for i, o in found if email is None or o == email]


async def _pass(now: datetime) -> dict[str, str]:
    from choregos_api.db.session import session_scope
    from choregos_api.services.assistant_tasks import run_due

    async with session_scope(orgs="*") as session:
        return await run_due(session, now)


async def _produce(work_item_id: str, **outputs: Any) -> None:
    """What the workflow's step leaves: a run and its artifacts."""
    from choregos_api.db.models import Project, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.artifacts import record_inline_outputs

    async with session_scope(orgs="*") as session:
        item = await session.get(WorkItem, work_item_id)
        assert item is not None
        run = Run(
            id=f"run-{work_item_id[:8]}",
            work_item_id=item.id,
            project_id=item.project_id,
            stage_role="brief",
            status="succeeded",
        )
        session.add(run)
        await session.flush()
        project = await session.get(Project, item.project_id)
        assert project is not None
        await record_inline_outputs(session, org_id=project.org_id, item=item, run=run, outputs=outputs)


async def as_admin(client: AsyncClient) -> None:
    await login(client, "admin@varga.dev")


async def _installation_id() -> str:
    from choregos_api.db.models import AssistantInstallation
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        return (await session.execute(select(AssistantInstallation.id))).scalar_one()
