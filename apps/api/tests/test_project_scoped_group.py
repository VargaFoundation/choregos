"""A human actor's group may name the project (S59-01): `{project}-maintainers` is answered, in each
project, by the people carrying that project's group, so one workflow shared to the organisation serves
every project with its own approvers.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .kits.human_actor import _decide, _member

WORKFLOW = """
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: release
  version: 1
initial: waiting
actors:
  maintainers: {type: human, group: "{project}-maintainers"}
states:
  waiting: {display: Waiting for a maintainer, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - id: t-ship
    from: waiting
    to: done
    by: maintainers
"""


async def _waiting(client: AsyncClient, pid: str) -> str:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    published = await client.put(f"/api/v1/projects/{pid}/workflows/release", json={"yaml": WORKFLOW})
    assert published.status_code == 200, published.text
    created = await client.post(
        f"/api/v1/projects/{pid}/work-items", json={"title": "Ship 1.2", "workflow": "release"}
    )
    assert created.status_code == 201, created.text
    item_id = str(created.json()["id"])
    async with session_scope() as session:
        session.add(
            HumanRequest(
                work_item_id=item_id,
                project_id=pid,
                transition_id="t-ship",
                kind="approval",
                payload={"summary": "Ship?"},
                requested_at=utcnow(),
            )
        )
    return item_id


async def test_the_step_is_answered_by_this_projects_maintainers_only(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    slug = project["slug"]
    item_id = await _waiting(client, project["id"])
    await _member(client, "other@varga.dev", "project_owner", ["atlas-maintainers"])
    await _member(client, "lead@varga.dev", "project_owner", [f"{slug}-maintainers"])

    refused = await _decide(app, "other@varga.dev", item_id)
    assert refused.status_code == 403, refused.text
    assert f"`{slug}-maintainers`" in refused.text, "the refusal names the project's group, resolved"
    answered = await _decide(app, "lead@varga.dev", item_id)
    assert answered.status_code in {200, 202}, answered.text


async def test_the_group_is_resolved_for_the_project_in_validation_and_members(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    slug, pid = project["slug"], project["id"]
    path = f"/api/v1/projects/{pid}/workflows/validate"
    await _member(client, "other@varga.dev", "project_owner", ["atlas-maintainers"])
    warned = (await client.post(path, json={"yaml": WORKFLOW})).json()
    unknown = [w for w in warned["warnings"] if w["code"] == "actor.group_unknown"]
    assert unknown and f"`{slug}-maintainers`" in unknown[0]["message"], warned["warnings"]

    await _member(client, "lead@varga.dev", "project_owner", [f"{slug}-maintainers"])
    known = (await client.post(path, json={"yaml": WORKFLOW})).json()
    assert not [w for w in known["warnings"] if w["code"] == "actor.group_unknown"], known["warnings"]

    members = await client.get(f"/api/v1/projects/{pid}/groups/{{project}}-maintainers/members")
    assert members.status_code == 200, members.text
    body = members.json()
    assert body["group"] == f"{slug}-maintainers"
    assert [m["email"] for m in body["members"]] == ["lead@varga.dev"]
