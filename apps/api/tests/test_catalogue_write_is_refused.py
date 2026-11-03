"""A catalogue tool that may write is neither listed nor called (S47-12, ADR 0034 §7).

Writes reach a third party only as governed actions. A deployment catalogue call is not one, so a
POST or remote MCP tool is dropped unless the deployment declares it `read_only: true`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

_HOST = "https://api.tickets.example"


def _catalogue(*, read_only: bool) -> str:
    flag = "    read_only: true\n" if read_only else ""
    return f"""
outils:
  - name: create_ticket
    description: Creates a ticket.
    provider: tickets
    input_schema: {{ type: object, properties: {{ title: {{ type: string }} }} }}
{flag}    http:
      method: POST
      url: {_HOST}/v1/tickets
      body: {{ title: "{{{{ title }}}}" }}
    price_eur: 0.01
  - name: list_tickets
    description: Lists tickets.
    provider: tickets
    http:
      method: GET
      url: {_HOST}/v1/tickets
"""


def _fake_provider(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Replace the provider's transport, and only it: the test client is an `httpx.AsyncClient` too."""
    seen: list[httpx.Request] = []
    original = httpx.AsyncClient.request

    async def fake(self: Any, *args: Any, **kwargs: Any) -> httpx.Response:
        url = str(args[1]) if len(args) > 1 else str(kwargs.get("url", ""))
        if not url.startswith(_HOST):
            return await original(self, *args, **kwargs)  # type: ignore[no-any-return]
        seen.append(httpx.Request(args[0], url, json=kwargs.get("json")))
        return httpx.Response(200, json={"id": "T-1"}, request=httpx.Request(args[0], url))

    monkeypatch.setattr(httpx.AsyncClient, "request", fake)
    return seen


async def _run(
    project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, read_only: bool
) -> tuple[str, dict[str, str]]:
    from choregos_api import catalogue as service_catalogue
    from choregos_api.db.models import Project, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    path = tmp_path / "catalogue.yaml"
    path.write_text(_catalogue(read_only=read_only), encoding="utf-8")
    monkeypatch.setenv("CHOREGOS_TOOL_CATALOG", str(path))
    service_catalogue.vider_cache()

    async with session_scope() as session:
        row = await session.get(Project, project["id"])
        assert row is not None
        config = dict(row.config or {})
        config["tools"] = ["create_ticket", "list_tickets"]
        row.config = config
        item = WorkItem(project_id=project["id"], tracker_key="varga/x#12", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="run-write-1",
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="custom",
                status="running",
            )
        )
    token = mint_run_token(
        "run-write-1", project_slug="billing-api", work_item_key="varga/x#12", ttl_minutes=30
    )
    return "run-write-1", {"Authorization": f"Bearer {token}"}


async def _ledger_rows(run_id: str) -> int:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from sqlalchemy import func, select

    async with session_scope() as session:
        count = await session.execute(
            select(func.count()).select_from(CostLedger).where(CostLedger.run_id == run_id)
        )
        return int(count.scalar_one())


async def test_a_post_catalogue_tool_is_refused_and_never_called(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, headers = await _run(project, tmp_path, monkeypatch, read_only=False)
    seen = _fake_provider(monkeypatch)

    response = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/create_ticket", json={"title": "x"}, headers=headers
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["errors"][0]["code"] == "catalogue_write_refused"
    assert "read_only: true" in body["detail"] and "MCP connector" in body["detail"]
    assert seen == [], "the provider must not be called"
    assert await _ledger_rows(run_id) == 0


async def test_a_tool_marked_read_only_is_called(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, headers = await _run(project, tmp_path, monkeypatch, read_only=True)
    seen = _fake_provider(monkeypatch)

    response = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/create_ticket", json={"title": "x"}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert len(seen) == 1


async def test_a_writing_tool_is_not_listed_anywhere(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.catalogue import outils_du_projet

    run_id, headers = await _run(project, tmp_path, monkeypatch, read_only=False)

    listing = await client.get(f"/api/v1/internal/runs/{run_id}/tools", headers=headers)
    assert listing.status_code == 200, listing.text
    names = {t["name"] for t in listing.json()["tools"]}
    assert "list_tickets" in names and "create_ticket" not in names

    console = await client.get(f"/api/v1/projects/{project['id']}/tools")
    assert console.status_code == 200, console.text
    by_name = {t["name"]: t for t in console.json()["tools"]}
    assert by_name["create_ticket"]["allowed"] is False
    assert by_name["list_tickets"]["allowed"] is True

    assert {o.name for o in outils_du_projet(["*"])} == {"list_tickets"}
    assert {o.name for o in outils_du_projet(["*"], include_writers=True)} == {
        "create_ticket",
        "list_tickets",
    }
