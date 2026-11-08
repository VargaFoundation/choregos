"""Which agents read a knowledge base (`used_by`): a run is served a base only through an agent whose
current revision names it, so the base's page says which ones do."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .agent_scopes_kit import ADMIN, ORG, as_user, create_agent

pytestmark = pytest.mark.usefixtures("admin")

BASE = f"{ORG}/knowledge"
READS = {"instructions": "Answer from the handbook.", "knowledge": [{"slug": "handbook"}]}
PLAIN = {"instructions": "Answer from memory."}


async def _agent(client: AsyncClient, slug: str, spec: dict[str, Any], **extra: Any) -> None:
    created = await create_agent(client, slug, **{"spec": spec, **extra})
    assert created.status_code == 201, created.text


async def _used_by(client: AsyncClient) -> list[dict[str, Any]]:
    read = await client.get(f"{BASE}/handbook")
    assert read.status_code == 200, read.text
    return list(read.json()["used_by"])


async def test_the_base_names_the_agents_whose_current_revision_reads_it(client: AsyncClient) -> None:
    await as_user(client, ADMIN)
    assert (await client.post(BASE, json={"slug": "handbook", "name": "Handbook"})).status_code == 201
    assert await _used_by(client) == [], "no agent reads it yet"

    await _agent(client, "support", READS, display_name="Support desk")
    await _agent(client, "triage", PLAIN)
    assert await _used_by(client) == [{"agent": "support", "display_name": "Support desk", "version": 1}]

    # A candidate revision that names it is not what runs: production (v1) does not read it.
    await _agent(client, "writer", PLAIN, release_policy={"on_publish": "candidate"})
    saved = await client.post(f"{ORG}/agents/writer/versions", json=READS)
    assert saved.status_code == 201, saved.text
    assert [user["agent"] for user in await _used_by(client)] == ["support"]

    # Production moving to a revision without it drops the agent.
    saved = await client.post(f"{ORG}/agents/support/versions", json=PLAIN)
    assert saved.status_code == 201, saved.text
    assert await _used_by(client) == []


async def test_a_draft_or_retired_agent_reads_nothing_and_a_listing_does_not_carry_it(
    client: AsyncClient,
) -> None:
    await as_user(client, ADMIN)
    assert (await client.post(BASE, json={"slug": "handbook", "name": "Handbook"})).status_code == 201
    await _agent(client, "drafted", READS, lifecycle="draft")
    await _agent(client, "old", READS)
    await _agent(client, "kept", READS)
    retired = await client.post(f"{ORG}/agents/old/lifecycle", json={"to": "retired", "reason": "replaced"})
    assert retired.status_code == 200, retired.text
    assert [user["agent"] for user in await _used_by(client)] == ["kept"]
    listed = await client.get(BASE)
    assert listed.json()[0]["used_by"] is None, "the listing stays one query per base cheaper"
