"""A task run of an assistant renders its agent's `config` and `preferences` (#1784, ADR 0137).

`task_instruction_values` gives the orchestrator's stage the installation's values and the subscriber's
profile memory for a task's work item, so that its model reads them, never the samples a version is checked
with at publication (`<house_name>`). Any other work item gets `None`: its run renders as before.
"""

# ruff: noqa: F811 - a fixture imported from a kit is redefined by the parameter that names it

from __future__ import annotations

from pathlib import Path
from typing import Any

from choregos_core.instructions import render_task_instructions
from httpx import AsyncClient

from .kits.assistant_memory import _profile
from .kits.assistant_tasks import (  # noqa: F401
    ME,
    TASKS,
    _installation_id,
    _ready,
    _subscriber,
    markets,
    offline,
)


async def _values(work_item_id: str) -> dict[str, Any] | None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services import task_instruction_values

    async with session_scope(orgs="*") as session:
        item = await session.get(WorkItem, work_item_id)
        assert item is not None
        return await task_instruction_values(session, item)


async def _installation_config() -> dict[str, Any]:
    from choregos_api.db.models import AssistantInstallation
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        installation = await session.get(AssistantInstallation, await _installation_id())
        assert installation is not None
        return dict(installation.config or {})


async def test_a_task_run_reads_the_installations_config_and_its_subscribers_preferences(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,
    markets: Any,
) -> None:
    await _ready(client)
    mine = await _subscriber(client, app, "mine@northwind.dev")
    await _profile(await _installation_id(), {"risk_tolerance": "low"}, email="mine@northwind.dev")
    await _profile(await _installation_id(), {"risk_tolerance": "high"}, email="admin@northwind.dev")
    ran = await mine.post(f"{TASKS}/morning_brief/run")
    assert ran.status_code == 201, ran.text

    values = await _values(ran.json()["work_item_id"])

    assert values is not None
    house = (await _installation_config())["house_name"]
    assert values["config"]["house_name"] == house
    assert values["preferences"] == {"risk_tolerance": "low"}, "the subscriber's profile, nobody else's"
    rendered = render_task_instructions(
        "{{ ticket.title }} for {{ config.house_name }}, risk {{ preferences.risk_tolerance }}."
        "{{ preferences.horizon_years | default(' No horizon.') }}",
        ticket={"title": "morning_brief"},
        **values,
    )
    assert rendered == f"morning_brief for {house}, risk low. No horizon."


async def test_a_work_item_that_is_not_a_tasks_has_no_assistant_values(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,
    markets: Any,
) -> None:
    await _ready(client)
    mine = await _subscriber(client, app, "mine@northwind.dev")
    ran = await mine.post(f"{TASKS}/morning_brief/run")
    assert ran.status_code == 201, ran.text
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        item = await session.get(WorkItem, ran.json()["work_item_id"])
        assert item is not None
        item.documents = {k: v for k, v in (item.documents or {}).items() if k != "task"}
    assert await _values(ran.json()["work_item_id"]) is None
