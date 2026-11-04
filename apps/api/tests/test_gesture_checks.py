# SPDX-License-Identifier: Apache-2.0
"""Can an INSTALLED extension refusede a human gesture (one project too many, an approval without a
fresh authentication) without the route acting?

The extension is a real module with its `.dist-info`, loaded by `load_extensions()`.
"""

from __future__ import annotations

import pathlib
import sys
import time
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

EXTENSION = """
import time

from choregos_api.extensions import GestureRefused, declare_gesture_check

STATE = {"max_projects": None, "freshness_s": None, "seen": []}


def project_cap(session, request):
    STATE["seen"].append(request)
    if STATE["max_projects"] is not None and STATE["max_projects"] <= 1:
        raise GestureRefused(f"{request.org} reached its project cap", "conflict")


def fresh_authentication(session, request):
    STATE["seen"].append(request)
    freshness = STATE["freshness_s"]
    if freshness is None:
        return
    when = request.principal.authenticated_at
    if when is None or time.time() - when > freshness:
        raise GestureRefused("approving takes an authentication less than 5 minutes old", "reauth")


def plug_in():
    declare_gesture_check("project.create", "cap", project_cap)
    declare_gesture_check("workitem.decision", "freshness", fresh_authentication)
    declare_gesture_check("release.approve", "freshness", fresh_authentication)
"""


@pytest.fixture
def extension(tmp_path: pathlib.Path) -> Iterator[dict[str, Any]]:
    from choregos_adapters import load_extensions
    from choregos_api.extensions import reset

    root = tmp_path / "site"
    root.mkdir()
    (root / "extension_gestures.py").write_text(EXTENSION, encoding="utf-8")
    info = root / "extension_gestures-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: extension_gestures\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        "[choregos.plugins]\nextension_gestures = extension_gestures:plug_in\n", encoding="utf-8"
    )
    sys.path.insert(0, str(root))
    try:
        assert "extension_gestures" in load_extensions()
        yield sys.modules["extension_gestures"].STATE
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("extension_gestures", None)
        reset()


def _project(slug: str) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": slug,
        "config": {
            "slug": slug,
            "org": "varga",
            "repo": {
                "url": f"https://github.com/varga/{slug}.git",
                "default_branch": "main",
                "language": "python",
            },
        },
    }


async def _count_projects() -> int:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        return int((await session.execute(select(func.count()).select_from(Project))).scalar_one())


async def test_one_project_too_many_is_refuseded_before_it_is_created(
    client: AsyncClient, project: dict[str, Any], extension: dict[str, Any]
) -> None:
    extension["max_projects"] = 1
    before = await _count_projects()
    refused = await client.post("/api/v1/orgs/varga/projects", json=_project("second"))
    assert refused.status_code == 409, refused.text
    assert "cap: varga reached its project cap" in refused.text
    assert await _count_projects() == before
    (request,) = extension["seen"]
    assert (request.gesture, request.org, request.target) == ("project.create", "varga", {"slug": "second"})

    extension["max_projects"] = None
    assert (await client.post("/api/v1/orgs/varga/projects", json=_project("second"))).status_code == 201


async def _human_request(client: AsyncClient, project: dict[str, Any]) -> str:
    from choregos_api.db.base import utcnow
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope

    await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    item = (
        await client.post(f"/api/v1/projects/{project['id']}/work-items", json={"title": "T", "size": "S"})
    ).json()
    async with session_scope(orgs="*") as session:
        session.add(
            HumanRequest(
                work_item_id=item["id"],
                project_id=project["id"],
                transition_id="t-prod",
                kind="approval",
                payload={"env": "prod"},
                requested_at=utcnow(),
            )
        )
    return str(item["id"])


async def test_an_approval_without_a_fresh_authentication_is_refused_with_401(
    client: AsyncClient, project: dict[str, Any], extension: dict[str, Any]
) -> None:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope

    item = await _human_request(client, project)
    extension["freshness_s"] = -1  # no session is fresh enough
    refused = await client.post(f"/api/v1/work-items/{item}/decisions", json={"kind": "approve"})
    assert refused.status_code == 401, refused.text
    assert "freshness: approving takes an authentication less than 5 minutes old" in refused.text
    assert "auth/login?reauth=1" in refused.text
    async with session_scope(orgs="*") as session:
        (human_request,) = (await session.execute(select(HumanRequest))).scalars()
        assert human_request.decided_at is None, "the decision was recorded despite the refusaled"
    view = extension["seen"][-1]
    assert view.target["transition_id"] == "t-prod" and view.target["request_payload"] == {"env": "prod"}
    # The session has just been opened: its `iat` is a few seconds old.
    assert view.principal.authenticated_at is not None and time.time() - view.principal.authenticated_at < 60

    extension["freshness_s"] = 300
    approved = await client.post(f"/api/v1/work-items/{item}/decisions", json={"kind": "approve"})
    assert approved.status_code == 202, approved.text


async def test_a_release_approval_goes_through_the_same_check(
    client: AsyncClient, project: dict[str, Any], extension: dict[str, Any]
) -> None:
    from choregos_api.db.models import Release
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        release = Release(project_id=project["id"], env="prod", status="awaiting_approval", items=[])
        session.add(release)
        await session.flush()
        release_id = str(release.id)
    extension["freshness_s"] = -1  # no session is fresh enough any more
    refused = await client.post(f"/api/v1/releases/{release_id}/approve", json={"note": "go"})
    assert refused.status_code == 401, refused.text
    assert extension["seen"][-1].target == {"project": "billing-api", "release": release_id, "env": "prod"}
    async with session_scope(orgs="*") as session:
        assert (await session.get(Release, release_id)).approved_by is None  # type: ignore[union-attr]


async def test_an_unknown_gesture_is_refused_at_declaration() -> None:
    from choregos_api.extensions import declare_gesture_check

    with pytest.raises(ValueError, match="geste inconnu"):  # lang: fr-ok - the message is pinned
        declare_gesture_check("project.delete", "x", lambda s, d: None)
