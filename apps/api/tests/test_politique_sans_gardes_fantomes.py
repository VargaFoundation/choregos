"""A policy promising a guard nobody applies is refused when saved (ADR 0044, ADR 0101).

Finding #286: the presets filled `approvals.*` and `review.require_human_for_risk`, nobody read
them. They were first logged as warnings; ADR 0101 refuses them at write time, key by key, with
what to do instead: declare the human in the workflow.
"""

from __future__ import annotations

from typing import Any

import structlog
from httpx import AsyncClient
from sqlalchemy import func, select

POLICY_THAT_PROMISES = """
apiVersion: choregos/v1
kind: Policy
metadata: { name: promises, version: 1 }
approvals:
  merge: { required: always, group: maintainers }
  spec: { required: never }
review: { require_human_for_risk: [high] }
"""


def _not_enforced(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("event") == "policy.not_enforced"]


async def _policy_rows(project_id: str) -> int:
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(PolicyDef).where(PolicyDef.project_id == project_id)
                )
            ).scalar_one()
        )


async def test_a_policy_that_promises_a_guard_is_refused_and_nothing_is_stored(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    before = await _policy_rows(project["id"])
    response = await client.put(
        f"/api/v1/projects/{project['id']}/policy", json={"yaml": POLICY_THAT_PROMISES}
    )
    assert response.status_code == 422, response.text
    codes = [e["code"] for e in response.json()["errors"]]
    assert codes == ["policy.approval_not_enforced", "policy.review_not_enforced"]
    assert await _policy_rows(project["id"]) == before


async def test_a_new_project_on_the_preset_warns_of_nothing(client: AsyncClient, admin: str) -> None:
    """The `solo` preset (a project without a template) used to write `approvals.spec|prod` and a
    human review: it promises nothing any more, and its policy raises no warning at birth."""
    with structlog.testing.capture_logs() as events:
        response = await client.post(
            "/api/v1/orgs/varga/projects",
            json={
                "slug": "no-promise",
                "name": "No promise",
                "config": {"slug": "no-promise", "org": "varga"},
            },
        )
        assert response.status_code == 201, response.text
        policy = await client.get(f"/api/v1/projects/{response.json()['id']}/policy")
    assert policy.json()["name"] == "solo"
    assert _not_enforced(events) == []
