"""Can an INSTALLED extension offer tools to the runs, through the catalogue's path and no other?

The extension is a real module with its `.dist-info`, loaded by `create_app()`: each test asks for
`extension_tools` BEFORE `client`, so before the `app` fixture builds the application. Its tools
must reach the agent like the catalogue's: announced by `GET /internal/runs/{id}/tools`, called by
`POST /internal/runs/{id}/tools/{name}` under the run's token, under the same cap on calls, with
the same line in the cost ledger. A name already taken is refused, not masked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from .kits import extension_tools as _kit
from .kits.extension_tools import run_extension_tool

pytestmark = pytest.mark.asyncio

OVERLAPPING_CATALOGUE = """
tools:
  - name: internal_directory
    description: The same name, on the catalogue side.
    provider: directory
    http: { method: GET, url: https://api.directory.example/v1/search }
"""

#: The installed extension; a test asks for it BEFORE `client`.
extension_tools = _kit.installed_extension


async def test_the_extension_tool_is_announced_to_the_run(
    extension_tools: None, client: AsyncClient, project: dict[str, Any]
) -> None:
    run_id, headers = await run_extension_tool(project["id"])
    response = await client.get(f"/api/v1/internal/runs/{run_id}/tools", headers=headers)
    assert response.status_code == 200, response.text
    assert [t["name"] for t in response.json()["tools"] if t["name"] != "web_fetch"] == ["internal_directory"]


async def test_the_call_goes_through_the_extension_and_is_counted(
    extension_tools: None, client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import CostLedger, Event
    from choregos_api.db.session import session_scope
    from choregos_contracts import EventType
    from sqlalchemy import select

    run_id, headers = await run_extension_tool(project["id"])
    response = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/internal_directory", headers=headers, json={"name": "Ada"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {
        "run": run_id,
        "project": "billing-api",
        "tool": "internal_directory",
        "arguments": {"name": "Ada"},
    }
    async with session_scope() as session:
        rows = (await session.execute(select(CostLedger).where(CostLedger.run_id == run_id))).scalars().all()
        calls = select(Event).where(Event.type == EventType.TOOL_CALLED.value)
        events = (await session.execute(calls)).scalars().all()
    assert [(row.kind, row.provider, row.model) for row in rows] == [
        ("tool", "greffon:extension_tools", "internal_directory")  # lang: fr-ok (a persisted prefix)
    ]
    assert [e.payload.get("tool") for e in events] == ["internal_directory"]


async def test_without_a_run_token_the_extension_tool_is_refused(
    extension_tools: None, client: AsyncClient, project: dict[str, Any]
) -> None:
    run_id, _ = await run_extension_tool(project["id"])
    refusal = await client.post(f"/api/v1/internal/runs/{run_id}/tools/internal_directory", json={})
    assert refusal.status_code == 401, refusal.text


async def test_a_name_already_taken_by_the_catalogue_is_refused_and_not_masked(
    extension_tools: None,
    client: AsyncClient,
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_api import catalogue as catalogue_service
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    file = tmp_path / "catalogue.yaml"
    file.write_text(OVERLAPPING_CATALOGUE, encoding="utf-8")
    monkeypatch.setenv("CHOREGOS_TOOL_CATALOG", str(file))
    catalogue_service.clear_cache()
    async with session_scope() as session:
        proj = await session.get(Project, project["id"])
        assert proj is not None
        proj.config = {**(proj.config or {}), "tools": ["internal_directory"]}
    try:
        run_id, headers = await run_extension_tool(project["id"])
        listing = await client.get(f"/api/v1/internal/runs/{run_id}/tools", headers=headers)
        call = await client.post(
            f"/api/v1/internal/runs/{run_id}/tools/internal_directory", headers=headers, json={}
        )
    finally:
        monkeypatch.delenv("CHOREGOS_TOOL_CATALOG")
        catalogue_service.clear_cache()
    assert listing.status_code == 409, listing.text
    assert call.status_code == 409, call.text
    assert "extension_tools" in call.text
