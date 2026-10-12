"""Serving mode `files` (S33-02, ADR 0051): a run is served the documents pinned when it was prepared."""

from __future__ import annotations

from typing import Any

from choregos_contracts import knowledge_digest
from httpx import AsyncClient

from . import knowledge_fixtures as fx

BASE = "/api/v1/orgs/varga/knowledge"


async def _run(project: dict[str, Any], knowledge: list[dict[str, Any]]) -> str:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="BILLING-9", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="run-kb",
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="implement",
                status="running",
                stage_input={"knowledge": knowledge},
            )
        )
    return mint_run_token("run-kb", project_slug="billing-api", work_item_key="BILLING-9", ttl_minutes=30)


async def _pinned(project: dict[str, Any]) -> list[dict[str, Any]]:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge.serving import pin
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        refs = await pin(session, org.id, project["slug"], ["architecture"])
    return [ref.model_dump(mode="json") for ref in refs]


async def test_a_run_reads_exactly_the_documents_it_was_prepared_with(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.knowledge.conversion import conversions

    await client.post(BASE, json={"slug": "architecture", "name": "Architecture framework"})
    deck = fx.pptx([("Network zoning principles", "Every zone has a firewall")])
    for name, data in (("zoning.pptx", deck), ("Zoning.md", b"# Zoning notes\n")):
        assert (
            await client.post(f"{BASE}/architecture/documents", files={"file": (name, data)})
        ).status_code == 202
    await conversions.drain()
    pinned = await _pinned(project)
    # Uploaded after the run was prepared: not served to it.
    await client.post(f"{BASE}/architecture/documents", files={"file": ("late.md", b"# Late\n")})
    await conversions.drain()

    token = await _run(project, pinned)
    client.cookies.clear()
    read = await client.get(
        "/api/v1/internal/runs/run-kb/knowledge", headers={"Authorization": f"Bearer {token}"}
    )
    assert read.status_code == 200, read.text
    (base,) = read.json()
    paths = [d["path"] for d in base["documents"]]
    assert sorted(paths) == ["zoning-2.md", "zoning.md"], "two files named alike stay apart"
    assert "late.md" not in paths
    assert base["digest"] == pinned[0]["digest"] == knowledge_digest(base["documents"])
    assert any("# Network zoning principles" in d["markdown"] for d in base["documents"])


async def test_a_base_no_longer_served_to_the_project_is_not_delivered(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await client.post(BASE, json={"slug": "architecture", "name": "Architecture framework"})
    pinned = await _pinned(project)
    await client.patch(f"{BASE}/architecture", json={"status": "archived"})
    token = await _run(project, pinned)
    client.cookies.clear()
    read = await client.get(
        "/api/v1/internal/runs/run-kb/knowledge", headers={"Authorization": f"Bearer {token}"}
    )
    assert read.status_code == 404


async def test_an_agent_names_its_knowledge_bases_without_changing_older_digests(
    client: AsyncClient, admin: str
) -> None:
    from choregos_api.schemas import AgentSpec
    from choregos_api.services.agents import empreinte

    before = empreinte(AgentSpec(instructions="x"))
    assert before == empreinte(AgentSpec(instructions="x", knowledge=[])), "an empty list changes nothing"
    assert before != empreinte(AgentSpec.model_validate({"instructions": "x", "knowledge": [{"slug": "kb"}]}))
    created = await client.post(
        "/api/v1/orgs/varga/agents",
        json={
            "slug": "architect",
            "display_name": "Architect",
            "spec": {"knowledge": [{"slug": "architecture"}]},
        },
    )
    assert created.status_code == 201, created.text
