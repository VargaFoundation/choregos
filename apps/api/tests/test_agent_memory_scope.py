"""An agent version says whose memory its runs use (S32-02): `none | project | agent`."""

from __future__ import annotations

import hashlib
import json

from httpx import AsyncClient


def test_the_default_scope_leaves_the_digest_of_older_versions_unchanged() -> None:
    from choregos_api.schemas import AgentSpec
    from choregos_api.services.agents import digest

    before = AgentSpec().model_dump(mode="json")
    del before["memory"]
    # Older than the knowledge field too (ADR 0051): an empty one is left out of the digest.
    if not before.get("knowledge"):
        before.pop("knowledge", None)
    # And older than the runtime (ADR 0063): none named, it is left out as well.
    before.pop("runtime")
    # And older than the network field (ADR 0070): left out while every part of it is unset.
    before.pop("network", None)
    expected = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(before, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
    )
    assert digest(AgentSpec()) == digest(AgentSpec(memory="project")) == expected
    assert digest(AgentSpec(memory="agent")) != expected, "a scope that changes the runs changes the digest"


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
