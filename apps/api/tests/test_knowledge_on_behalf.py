"""A run reads a restricted knowledge base on behalf of the person who started it (S42-11e, ADR 0097).

The run is served a restricted base only when its initiator reads it (a grant, a group, ownership,
an administrator); a fan-out child acts for its parent's initiator; a run with nobody behind it is
refused. Checked when the run is prepared and again at every read.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login
from .test_knowledge_files import _pinned, _run
from .test_knowledge_index import _indexed_base
from .test_knowledge_rights import ORG, _uid

READER = "person:reader@varga.dev"


async def _restricted(client: AsyncClient, slug: str = "architecture", **fields: Any) -> None:
    created = await client.post(ORG, json={"slug": slug, "name": "A", "restricted": True, **fields})
    assert created.status_code == 201, created.text


async def _grant_to(client: AsyncClient, *grants: dict[str, str], slug: str = "architecture") -> None:
    await login(client, "admin@varga.dev")
    put = await client.put(f"{ORG}/{slug}/grants", json={"grants": list(grants)})
    assert put.status_code == 200, put.text


async def _parent(project: dict[str, Any], key: str, initiated_by: str | None) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(
            WorkItem(
                project_id=project["id"], tracker_key=key, title="P", state="ready", initiated_by=initiated_by
            )
        )


@pytest.mark.parametrize(
    "initiated_by",
    [
        "schedule:nightly",
        "tracker:github",
        "a2a:partner",
        "system",
        None,
        "person:",
        "person:nobody@varga.dev",
    ],
)
async def test_a_run_with_no_reader_behind_it_is_refused_a_restricted_base(
    client: AsyncClient, admin: str, project: dict[str, Any], initiated_by: str | None
) -> None:
    from choregos_api.knowledge.serving import KnowledgeRefusedError

    await _restricted(client)
    with pytest.raises(KnowledgeRefusedError, match="restricted"):
        await _pinned(project, initiated_by)


async def test_a_base_that_is_not_restricted_is_served_whoever_started_the_run(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    created = await client.post(ORG, json={"slug": "architecture", "name": "A"})
    assert created.status_code == 201, created.text
    assert (await _pinned(project, "schedule:nightly"))[0]["slug"] == "architecture"


async def test_the_initiator_reads_through_a_user_a_group_or_a_project_grant(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    from choregos_api.knowledge.serving import KnowledgeRefusedError

    await _restricted(client)
    uid = await _uid(client, "reader@varga.dev", org_role="viewer", groups=["hr"])
    with pytest.raises(KnowledgeRefusedError):
        await _pinned(project, READER)
    for grant in (
        {"subject_type": "user", "subject": uid, "role": "viewer"},
        {"subject_type": "group", "subject": "hr", "role": "viewer"},
    ):
        await _grant_to(client, grant)
        assert (await _pinned(project, READER))[0]["slug"] == "architecture", grant
    # a project grant counts for a member of that project, not for an organisation-wide viewer
    await _grant_to(client, {"subject_type": "project", "subject": project["slug"], "role": "viewer"})
    with pytest.raises(KnowledgeRefusedError):
        await _pinned(project, READER)
    await _uid(client, "member@varga.dev", projects={project["slug"]: "developer"})
    assert await _pinned(project, "mcp:member@varga.dev")


async def test_an_administrator_reads_every_restricted_base(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    await _restricted(client)
    assert await _pinned(project, "person:admin@varga.dev")


async def test_a_fan_out_child_acts_for_its_parents_initiator(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    from choregos_api.knowledge.serving import KnowledgeRefusedError

    await _restricted(client)
    uid = await _uid(client, "reader@varga.dev", org_role="viewer")
    await _grant_to(client, {"subject_type": "user", "subject": uid, "role": "viewer"})
    await _parent(project, "BILLING-1", READER)
    await _parent(project, "BILLING-2", "work_item:BILLING-1")
    assert await _pinned(project, "work_item:BILLING-2"), "a grandchild follows the chain"
    await _parent(project, "BILLING-3", "schedule:nightly")
    with pytest.raises(KnowledgeRefusedError, match="acts for nobody"):
        await _pinned(project, "work_item:BILLING-3")
    await _parent(project, "LOOP", "work_item:LOOP")
    with pytest.raises(KnowledgeRefusedError, match="acts for nobody"):
        await _pinned(project, "work_item:LOOP")


async def test_a_grant_revoked_during_the_run_holds_at_its_next_files_read(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    await _restricted(client)
    uid = await _uid(client, "reader@varga.dev", org_role="viewer")
    await _grant_to(client, {"subject_type": "user", "subject": uid, "role": "viewer"})
    token = await _run(project, await _pinned(project, READER), READER)
    headers = {"Authorization": f"Bearer {token}"}
    client.cookies.clear()
    assert (await client.get("/api/v1/internal/runs/run-kb/knowledge", headers=headers)).status_code == 200
    await _grant_to(client)
    client.cookies.clear()
    assert (await client.get("/api/v1/internal/runs/run-kb/knowledge", headers=headers)).status_code == 404


async def test_a_grant_revoked_during_the_run_holds_at_its_next_search(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    await _indexed_base(client, "architecture", restricted=True)
    uid = await _uid(client, "reader@varga.dev", org_role="viewer")
    await _grant_to(client, {"subject_type": "user", "subject": uid, "role": "viewer"})
    token = await _run(project, [{"slug": "architecture", "mode": "index"}], READER)
    headers = {"Authorization": f"Bearer {token}"}

    async def hits() -> int:
        client.cookies.clear()
        answer = await client.post(
            "/api/v1/internal/runs/run-kb/knowledge/search", json={"query": "TLS"}, headers=headers
        )
        assert answer.status_code == 200, answer.text
        return len(answer.json()["hits"])

    assert await hits() > 0
    await _grant_to(client)
    assert await hits() == 0
