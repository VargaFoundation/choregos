# SPDX-License-Identifier: Apache-2.0
"""Helpers shared by the tests of the plugin: packages, reports, projects and runs."""

from __future__ import annotations

import json
import pathlib
from typing import Any

import httpx
from httpx import AsyncClient

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
CORE_REF = FIXTURES / "core-ref"
IT4IT = FIXTURES / "it4it"


async def sign_in(http: AsyncClient, email: str, *, reauth: bool = False) -> None:
    """Development sign-in: `?as=email` opens a session without an identity provider."""
    params = {"as": email, **({"reauth": "1"} if reauth else {})}
    start = await http.get("/api/v1/auth/login", params=params)
    assert start.status_code == 307, start.text
    assert (await http.get(start.headers["location"])).status_code == 307


async def create_project(http: AsyncClient, slug: str) -> dict[str, Any]:
    response = await http.post(
        "/api/v1/orgs/varga/projects",
        json={
            "slug": slug,
            "name": slug,
            "config": {"slug": slug, "org": "varga", "repo": {"url": f"https://github.com/varga/{slug}.git"}},
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def package_files(root: pathlib.Path = CORE_REF) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.yaml"))
    }


def report(*lines: dict[str, Any], end: dict[str, Any] | None = None) -> str:
    body = [json.dumps(line) for line in lines]
    body.append(json.dumps(end if end is not None else {"_end": True, "lines": len(lines)}))
    return "\n".join(body) + "\n"


def line(check: str, scope: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"layer": "os", "check": check, "scope": scope, "status": status, **extra}


async def post_report(http: AsyncClient, project: dict[str, Any], text: str) -> httpx.Response:
    return await http.post(
        f"/api/v1/projects/{project['id']}/observations",
        content=text.encode(),
        headers={"Content-Type": "application/x-ndjson"},
    )


async def stored_objects(project_id: str, type_: str = "finding") -> dict[str, tuple[int, dict[str, Any]]]:
    """The state in the database, read under the plugin: {id: (row_version, properties)}."""
    from choregos_api.db.session import session_scope
    from choregos_ontology.service.store import ManagedObject
    from sqlalchemy import select

    async with session_scope() as session:
        of_type = ManagedObject.project_id == project_id, ManagedObject.object_type == type_
        rows = await session.execute(select(ManagedObject).where(*of_type))
        return {o.id: (o.row_version, dict(o.properties)) for o in rows.scalars()}


async def create_run(project: dict[str, Any], run_id: str) -> dict[str, str]:
    """A run of the project, and its token's header — as the orchestrator mints them."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    key = f"varga/{run_id}#1"
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key=key, title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(id=run_id, work_item_id=item.id, project_id=project["id"], stage_role="analyse_infra")
        )
    token = mint_run_token(run_id, project_slug=project["slug"], work_item_key=key, ttl_minutes=30)
    return {"Authorization": f"Bearer {token}"}


async def call_tool(
    http: AsyncClient, run_id: str, headers: dict[str, str], tool: str, arguments: dict[str, Any]
) -> Any:
    """A tool call through the internal API, as `choregos-tools` relays it: returns the JSON body."""
    response = await http.post(
        f"/api/v1/internal/runs/{run_id}/tools/{tool}", headers=headers, json=arguments
    )
    assert response.status_code == 200, response.text
    return response.json()
