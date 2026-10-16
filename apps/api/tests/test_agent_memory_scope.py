"""An agent version says whose memory its runs use (S32-02): `none | project | agent`."""

from __future__ import annotations

import hashlib
import json

from httpx import AsyncClient


def test_the_default_scope_leaves_the_digest_of_older_versions_unchanged() -> None:
    from choregos_api.schemas import AgentSpec
    from choregos_api.services.agents import empreinte

    avant = AgentSpec().model_dump(mode="json")
    del avant["memory"]
    # Older than the knowledge field too (ADR 0051): an empty one is left out of the digest.
    if not avant.get("knowledge"):
        avant.pop("knowledge", None)
    # And older than the network field (ADR 0070): left out while every part of it is unset.
    avant.pop("network", None)
    attendu = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(avant, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
    )
    assert empreinte(AgentSpec()) == empreinte(AgentSpec(memory="project")) == attendu
    assert empreinte(AgentSpec(memory="agent")) != attendu, "a scope that changes the runs changes the digest"


async def test_a_version_declares_its_memory_scope(client: AsyncClient, admin: str) -> None:
    created = await client.post(
        "/api/v1/orgs/varga/agents",
        json={"slug": "accountant", "display_name": "Accountant", "spec": {"memory": "agent"}},
    )
    assert created.status_code == 201, created.text
    version = (await client.get("/api/v1/orgs/varga/agents/accountant/versions/1")).json()
    assert version["spec"]["memory"] == "agent"
    refused = await client.post("/api/v1/orgs/varga/agents/accountant/versions", json={"memory": "team"})
    assert refused.status_code == 422
