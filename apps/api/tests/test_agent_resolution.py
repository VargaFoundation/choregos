"""Which agent version a project's run would load for each reference (S39-13a).

The endpoint delegates to the runtime's own resolver, so the console's answer is the run's answer.
"""

from __future__ import annotations

from typing import Any

from httpx import ASGITransport, AsyncClient

ORG = "/api/v1/orgs/varga"


async def _agent(client: AsyncClient, slug: str, **extra: Any) -> None:
    created = await client.post(f"{ORG}/agents", json={"slug": slug, "display_name": slug.title(), **extra})
    assert created.status_code == 201, created.text


async def _resolve(client: AsyncClient, project_id: str, *refs: str) -> Any:
    return await client.get(
        f"/api/v1/projects/{project_id}/agent-resolution", params=[("ref", r) for r in refs]
    )


async def test_a_pin_wins_over_the_latest_and_an_explicit_version_wins_over_the_pin(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _agent(client, "developer")
    published = await client.post(f"{ORG}/agents/developer/versions", json={})
    assert published.status_code == 201, published.text
    pin = await client.put(f"/api/v1/projects/{project['id']}/agents/developer", json={"version": 1})
    assert pin.status_code == 200, pin.text
    answer = await _resolve(client, project["id"], "developer", "developer@2")
    assert answer.status_code == 200, answer.text
    first, second = answer.json()
    assert (first["status"], first["version"], first["pinned"], first["display_name"]) == (
        "resolved",
        1,
        True,
        "Developer",
    )
    assert (second["ref"], second["status"], second["version"], second["pinned"]) == (
        "developer@2",
        "resolved",
        2,
        False,
    )
    assert (await client.delete(f"/api/v1/projects/{project['id']}/agents/developer")).status_code == 204
    latest = (await _resolve(client, project["id"], "developer")).json()[0]
    assert (latest["version"], latest["pinned"]) == (2, False), "no pin: the latest version"


async def test_a_draft_agent_is_unavailable_with_the_runtime_s_reason(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _agent(client, "spec-writer", lifecycle="draft")
    answer = (await _resolve(client, project["id"], "spec-writer")).json()[0]
    assert answer["status"] == "unavailable" and answer["lifecycle"] == "draft"
    assert "draft" in answer["reason"] and answer["version"] is None


async def test_an_absent_agent_is_not_installed_and_says_whether_the_catalogue_has_it(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    answers = (await _resolve(client, project["id"], "docs-writer", "nobody-here")).json()
    by_slug = {a["slug"]: a for a in answers}
    assert (
        by_slug["nobody-here"]["status"] == "not_installed"
        and by_slug["nobody-here"]["in_catalogue"] is False
    )
    assert by_slug["docs-writer"]["status"] == "not_installed"
    assert by_slug["docs-writer"]["in_catalogue"] is True, "publishing a workflow installs it (ADR 0040)"


async def test_a_reference_is_validated(client: AsyncClient, project: dict[str, Any]) -> None:
    assert (await _resolve(client, project["id"], "Dev")).status_code == 422
    assert (await _resolve(client, project["id"], "developer@0")).status_code == 422
    assert (await _resolve(client, project["id"])).status_code == 422, "at least one reference"
    assert (await _resolve(client, project["id"], *[f"agent-{i}" for i in range(65)])).status_code == 422
    assert (await _resolve(client, project["id"], *[f"agent-{i}" for i in range(64)])).status_code == 200


async def test_a_stranger_gets_what_the_implicit_agents_endpoint_gives(
    app: Any, project: dict[str, Any]
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anonymous:
        base = f"/api/v1/projects/{project['id']}"
        reference = await anonymous.get(f"{base}/implicit-agents")
        resolution = await anonymous.get(f"{base}/agent-resolution", params={"ref": "developer"})
    assert reference.status_code in {401, 403, 404}
    assert resolution.status_code == reference.status_code


async def test_a_canary_agent_says_why_and_how_much(client: AsyncClient, project: dict[str, Any]) -> None:
    """S40-02: the answer carries the source, the channel and the testing share (ADR 0071)."""
    await _agent(
        client,
        "canary-agent",
        release_policy={"on_publish": "candidate", "stable": {"require_candidate": True}},
    )
    assert (await client.post(f"{ORG}/agents/canary-agent/versions", json={})).status_code == 201
    sent = await client.post(
        f"{ORG}/agents/canary-agent/releases", json={"channel": "candidate", "version": 2, "percent": 10}
    )
    assert sent.status_code == 201, sent.text
    answer = (await _resolve(client, project["id"], "canary-agent", "canary-agent@2")).json()
    production, explicit = answer
    assert (production["version"], production["source"], production["channel"]) == (1, "stable", "stable")
    assert (production["candidate_version"], production["candidate_percent"]) == (2, 10)
    assert production["pinned"] is False
    assert (explicit["version"], explicit["source"], explicit["channel"]) == (2, "explicit", None)
