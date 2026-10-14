"""The lifecycle of an agent (S32-04): draft → active → deprecated → retired.

Retiring revokes the agent's credentials, removes its project pins and refuses new runs; it is
refused while one of its runs is in flight, unless forced. The lifecycle stays apart from the
operational `status`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient

AGENTS = "/api/v1/orgs/varga/agents"


async def _create(client: AsyncClient, slug: str = "onboarding-coordinator", **extra: Any) -> Any:
    created = await client.post(AGENTS, json={"slug": slug, "display_name": slug, **extra})
    assert created.status_code == 201, created.text
    return created.json()


async def _move(client: AsyncClient, to: str, slug: str = "onboarding-coordinator", **extra: Any) -> Any:
    return await client.post(f"{AGENTS}/{slug}/lifecycle", json={"to": to, **extra})


async def _resolve(project_id: str, slug: str = "onboarding-coordinator") -> Any:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope
    from choregos_api.services.agents import resoudre_l_agent

    async with session_scope() as session:
        projet = await session.get(Project, project_id)
        assert projet is not None
        return await resoudre_l_agent(session, projet.org_id, projet.id, slug)


async def _run(
    project_id: str, status: str, key: str = "BILLING-1", slug: str = "onboarding-coordinator"
) -> None:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = WorkItem(project_id=project_id, tracker_key=key, title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id=f"{key}-implement-1",
                work_item_id=item.id,
                project_id=project_id,
                stage_role="implement",
                status=status,
                agent_slug=slug,
                agent_version=1,
            )
        )


async def test_a_draft_is_neither_pinned_nor_run_until_activated(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.services.agents import AgentIndisponible

    assert (await _create(client, lifecycle="draft"))["lifecycle"] == "draft"
    pin = await client.put(
        f"/api/v1/projects/{project['id']}/agents/onboarding-coordinator", json={"version": 1}
    )
    assert pin.status_code == 409 and "draft" in pin.text
    with pytest.raises(AgentIndisponible, match="draft"):
        await _resolve(project["id"])
    assert (await _move(client, "deprecated")).status_code == 409, "a draft is activated or retired"
    activated = await _move(client, "active")
    assert activated.status_code == 200 and activated.json()["agent"]["lifecycle"] == "active"
    assert (await _resolve(project["id"])).version == 1


async def test_a_deprecated_agent_still_runs_and_its_pin_warns(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _create(client)
    assert (await _move(client, "deprecated", reason="replaced by onboarding-v2")).status_code == 200
    pin = await client.put(
        f"/api/v1/projects/{project['id']}/agents/onboarding-coordinator", json={"version": 1}
    )
    assert pin.status_code == 200, pin.text
    assert "deprecated" in (pin.json()["warning"] or "")
    assert (await _resolve(project["id"])).version == 1, "deprecated is not retired: it still runs"
    assert (await _move(client, "active")).json()["agent"]["lifecycle"] == "active"


async def test_retiring_runs_its_checklist_and_refuses_new_runs(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.services.agents import AgentIndisponible

    await _create(client, kind="external")
    token = await client.post("/api/v1/me/tokens", json={"name": "claude-code", "scopes": ["mcp:read"]})
    attached = await client.post(
        f"{AGENTS}/onboarding-coordinator/credentials", json={"kind": "token", "token_id": token.json()["id"]}
    )
    assert attached.status_code == 201, attached.text
    pin = await client.put(
        f"/api/v1/projects/{project['id']}/agents/onboarding-coordinator", json={"version": 1}
    )
    assert pin.status_code == 200

    retired = await _move(client, "retired", reason="the process moved to HR")
    assert retired.status_code == 200, retired.text
    body = retired.json()
    assert body["agent"]["lifecycle"] == "retired" and body["agent"]["retired_at"]
    assert (body["revoked_credentials"], body["unpinned_projects"]) == (1, ["billing-api"])
    credentials = (await client.get(f"{AGENTS}/onboarding-coordinator/credentials")).json()
    assert all(c["revoked_at"] for c in credentials)
    assert (await client.get(f"/api/v1/projects/{project['id']}/agents")).json() == []
    with pytest.raises(AgentIndisponible, match="retired"):
        await _resolve(project["id"])
    assert body["agent"]["status"] == "active", "the operational status is another column"

    # Final: no way back, no new version, no pin.
    assert (await _move(client, "active")).status_code == 409
    assert (await client.post(f"{AGENTS}/onboarding-coordinator/versions", json={})).status_code == 409
    again = await client.put(
        f"/api/v1/projects/{project['id']}/agents/onboarding-coordinator", json={"version": 1}
    )
    assert again.status_code == 409
    audit = (await client.get("/api/v1/audit", params={"target_type": "agent"})).json()["items"]
    (entry,) = [e for e in audit if e["action"] == "agent.retire"]
    assert (entry["payload"]["revoked_credentials"], entry["payload"]["after"]) == (1, "retired")


async def test_retiring_waits_for_runs_in_flight_unless_forced(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _create(client)
    await _run(project["id"], "running")
    refused = await _move(client, "retired")
    assert refused.status_code == 409 and "BILLING-1" in refused.text
    forced = await _move(client, "retired", force=True)
    assert forced.status_code == 200, forced.text
    assert (forced.json()["forced"], forced.json()["running_work_items"]) == (True, ["BILLING-1"])


async def test_a_finished_run_does_not_hold_the_retirement(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _create(client)
    await _run(project["id"], "succeeded")
    assert (await _move(client, "retired")).status_code == 200


async def test_an_agent_unused_for_60_days_says_so(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import Agent
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from sqlalchemy import select

    await _create(client)
    assert (await client.get(f"{AGENTS}/onboarding-coordinator")).json()["unused"] is False, "new, not unused"
    async with session_scope() as session:
        agent = (await session.execute(select(Agent))).scalar_one()
        agent.created_at = utcnow() - timedelta(days=61)
    stale = (await client.get(f"{AGENTS}/onboarding-coordinator")).json()
    assert (stale["unused"], stale["last_run_at"]) == (True, None)
    await _run(project["id"], "succeeded")
    used = (await client.get(f"{AGENTS}/onboarding-coordinator")).json()
    assert used["unused"] is False and used["last_run_at"]


async def test_ownership_moves_and_the_credentials_stay(client: AsyncClient, admin: str) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await _create(client, kind="external")
    token = await client.post("/api/v1/me/tokens", json={"name": "claude-code", "scopes": ["mcp:read"]})
    await client.post(
        f"{AGENTS}/onboarding-coordinator/credentials", json={"kind": "token", "token_id": token.json()["id"]}
    )
    async with session_scope() as session:
        org = (await session.execute(select(Organization))).scalar_one()
        user = User(email="lea@varga.dev", display_name="Léa")
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=org.id, role="developer"))
        session.add(User(email="stranger@else.dev", display_name="Stranger"))
    moved = await client.post(f"{AGENTS}/onboarding-coordinator/owner", json={"email": "Lea@varga.dev"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["owner"] == "lea@varga.dev"
    credentials = (await client.get(f"{AGENTS}/onboarding-coordinator/credentials")).json()
    assert len(credentials) == 1 and credentials[0]["revoked_at"] is None
    stranger = await client.post(
        f"{AGENTS}/onboarding-coordinator/owner", json={"email": "stranger@else.dev"}
    )
    assert stranger.status_code == 422
    audit = (await client.get("/api/v1/audit", params={"target_type": "agent"})).json()["items"]
    assert any(e["action"] == "agent.transfer" for e in audit)
