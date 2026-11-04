"""The agent registry of the organisation (ADR 0033, S18-01).

An agent is an object of the organisation; what it IS lives in its versions, which are never
modified. A project pins a version and can only tighten it.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login

SPEC: dict[str, Any] = {
    "instructions": "Prepares the access plan of an arrival: accounts, groups, equipment.",
    "model": "profile:standard",
    "limits": {"max_turns": 40, "max_minutes": 30},
    "budget": {"run_usd": 2.0, "daily_usd": 20.0, "monthly_usd": None},
    "skills": [{"slug": "onboarding-procedure"}],
    "mcp_servers": [{"connector": "entra", "tools": ["entra_read_*", "entra_create_account"]}],
}


async def _create(client: AsyncClient, slug: str = "onboarding-coordinator", **extra: Any) -> Any:
    return await client.post(
        "/api/v1/orgs/varga/agents",
        json={"slug": slug, "display_name": "Onboarding coordinator", "spec": SPEC, **extra},
    )


async def test_an_agent_is_born_with_its_version_1(client: AsyncClient, admin: str) -> None:
    created = await _create(client)
    assert created.status_code == 201, created.text
    agent = created.json()
    assert (agent["kind"], agent["status"], agent["latest_version"], agent["owner"]) == (
        "internal",
        "active",
        1,
        admin,
    )
    assert agent["versions"][0]["spec"]["budget"] == {"run_usd": 2.0, "daily_usd": 20.0, "monthly_usd": None}
    assert [a["slug"] for a in (await client.get("/api/v1/orgs/varga/agents")).json()] == [
        "onboarding-coordinator"
    ]
    assert (await _create(client)).status_code == 409, "one slug per organisation"


async def test_publishing_never_rewrites_a_version(client: AsyncClient, admin: str) -> None:
    await _create(client)
    v1 = (await client.get("/api/v1/orgs/varga/agents/onboarding-coordinator/versions/1")).json()
    stricter = {**SPEC, "budget": {"run_usd": 1.0, "daily_usd": 10.0}}
    v2 = await client.post("/api/v1/orgs/varga/agents/onboarding-coordinator/versions", json=stricter)
    assert v2.status_code == 201 and v2.json()["version"] == 2, v2.text
    assert v2.json()["checksum"] != v1["checksum"]
    # Where a revision sits (`channels`) moves; what it IS never does (ADR 0071).
    v1_after = (await client.get("/api/v1/orgs/varga/agents/onboarding-coordinator/versions/1")).json()
    assert v1_after.pop("channels") == [] and v1.pop("channels") == ["stable"]
    assert v1_after == v1
    agent = (await client.get("/api/v1/orgs/varga/agents/onboarding-coordinator")).json()
    assert [v["version"] for v in agent["versions"]] == [2, 1] and agent["latest_version"] == 2


async def test_revocation_is_final(client: AsyncClient, admin: str) -> None:
    await _create(client)
    revoked = await client.patch(
        "/api/v1/orgs/varga/agents/onboarding-coordinator", json={"status": "revoked"}
    )
    assert revoked.status_code == 200 and revoked.json()["revoked_at"], revoked.text
    publish = await client.post("/api/v1/orgs/varga/agents/onboarding-coordinator/versions", json=SPEC)
    assert publish.status_code == 409
    reactivate = await client.patch(
        "/api/v1/orgs/varga/agents/onboarding-coordinator", json={"status": "active"}
    )
    assert reactivate.status_code == 409


async def test_a_developer_reads_the_registry_but_does_not_write_to_it(
    client: AsyncClient, admin: str
) -> None:
    await _create(client)
    member = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "dev@varga.dev", "role": "developer"}
    )
    assert member.status_code in {200, 201}
    await login(client, "dev@varga.dev")
    assert (await client.get("/api/v1/orgs/varga/agents")).status_code == 200
    assert (await _create(client, slug="other")).status_code == 403
    assert (
        await client.post("/api/v1/orgs/varga/agents/onboarding-coordinator/versions", json=SPEC)
    ).status_code == 403


async def test_a_project_tightens_and_cannot_widen(client: AsyncClient, project: dict[str, Any]) -> None:
    await _create(client)
    pin = f"/api/v1/projects/{project['id']}/agents/onboarding-coordinator"
    tightened = await client.put(
        pin,
        json={"version": 1, "overrides": {"budget": {"run_usd": 1.5}, "tools": ["entra_read_*"]}},
    )
    assert tightened.status_code == 200, tightened.text
    effective = tightened.json()["effective"]
    assert effective["budget"] == {"run_usd": 1.5, "daily_usd": 20.0, "monthly_usd": None}
    assert effective["mcp_servers"] == [{"connector": "entra", "tools": ["entra_read_*"]}]

    for widened in (
        {"budget": {"daily_usd": 50.0}},
        {"limits": {"max_turns": 100}},
        {"tools": ["entra_delete_account"]},
    ):
        refused = await client.put(pin, json={"version": 1, "overrides": widened})
        assert refused.status_code == 422, (widened, refused.text)
    pins = (await client.get(f"/api/v1/projects/{project['id']}/agents")).json()
    assert [(e["agent"], e["version"]) for e in pins] == [("onboarding-coordinator", 1)]
    assert (await client.delete(pin)).status_code == 204
    assert (await client.get(f"/api/v1/projects/{project['id']}/agents")).json() == []


async def test_an_agent_of_another_organisation_cannot_be_pinned(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import Agent, AgentVersion, Organization
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        other = Organization(slug="other", name="Autre")
        session.add(other)
        await session.flush()
        agent = Agent(org_id=other.id, slug="spy", kind="internal", display_name="Spy", status="active")
        session.add(agent)
        await session.flush()
        session.add(AgentVersion(agent_id=agent.id, org_id=other.id, version=1, spec={}, checksum="sha256:x"))
    response = await client.put(f"/api/v1/projects/{project['id']}/agents/spy", json={"version": 1})
    assert response.status_code == 404, response.text
    assert (await client.get("/api/v1/orgs/other/agents")).status_code == 403, "not a member of `other`"


async def test_instructions_that_do_not_render_are_refused_at_publication(
    client: AsyncClient, admin: str
) -> None:
    """The sandbox (ADR 0033): an escape, an unknown variable, a broken syntax."""
    for instructions in ("{{ ''.__class__.__mro__ }}", "{{ unknown }}", "{% for x in %}"):
        refused = await _create(client, spec={**SPEC, "instructions": instructions})
        assert refused.status_code == 422, (instructions, refused.text)
    assert (await _create(client)).status_code == 201
    escape = {**SPEC, "instructions": "{{ cycler.__init__.__globals__ }}"}
    publish = await client.post("/api/v1/orgs/varga/agents/onboarding-coordinator/versions", json=escape)
    assert publish.status_code == 422, publish.text


async def test_the_metrics_of_an_agent_count_its_failures_and_its_tools(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import CostLedger, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    await _create(client)
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="BILLING-1", title="T", state="ready")
        session.add(item)
        await session.flush()
        for rank, state in enumerate(("succeeded", "succeeded", "failed")):
            session.add(
                Run(
                    id=f"run-{rank}",
                    work_item_id=item.id,
                    project_id=project["id"],
                    stage_role="implement",
                    status=state,
                    agent_slug="onboarding-coordinator",
                    agent_version=1,
                )
            )
        await session.flush()
        for kind_, amount in (("model", 1.0), ("tool", 0.25)):
            session.add(
                CostLedger(project_id=project["id"], run_id="run-0", kind=kind_, cost_usd=amount, ts=utcnow())
            )
    metrics = (await client.get("/api/v1/orgs/varga/agents/onboarding-coordinator/metrics")).json()
    assert (metrics["runs"], metrics["succeeded"], metrics["failed"]) == (3, 2, 1)
    assert metrics["success_rate"] == pytest.approx(2 / 3, abs=1e-3), "a failed run lowers the rate"
    assert metrics["cost_by_kind"] == {"model": 1.0, "tool": 0.25}, "a tool call counts in the cost"
    assert metrics["cost_usd"] == pytest.approx(1.25)
    assert metrics["by_project"] == [{"project": "billing-api", "runs": 3, "cost_usd": 1.25}]
    assert metrics["spent_today_usd"] == pytest.approx(1.25) and metrics["daily_budget_usd"] == 20.0
