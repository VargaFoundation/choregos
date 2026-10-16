"""An agent version binds several memories (S40-11a, ADR 0075): publication, derivation, checksums."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from httpx import AsyncClient

from .agent_scopes_kit import ORG, SPEC, as_user, create_agent, make_project

MEM = f"{ORG}/memories"
FOUR = [
    {"scope": "global", "slug": "security-lessons", "mode": "read"},
    {"scope": "global", "slug": "architecture-decisions", "mode": "read"},
    {"scope": "project", "slug": "decisions", "mode": "read"},
    {"scope": "project", "slug": "shared", "mode": "write"},
]


async def _global_memories(client: AsyncClient) -> None:
    for slug in ("security-lessons", "architecture-decisions"):
        assert (await client.post(MEM, json={"slug": slug, "display_name": slug})).status_code == 201


async def test_a_version_binds_two_global_and_two_project_memories(client: AsyncClient, admin: str) -> None:
    await _global_memories(client)
    created = await create_agent(client, "architect", spec={**SPEC, "memories": FOUR})
    assert created.status_code == 201, created.text
    version = (await client.get(f"{ORG}/agents/architect/versions/1")).json()
    assert version["spec"]["memories"] == FOUR
    assert version["effective_memories"] == FOUR
    assert "memory" not in version["spec"] or version["spec"]["memory"] == "project"


async def test_an_unknown_global_memory_is_refused_by_name(client: AsyncClient, admin: str) -> None:
    refused = await create_agent(
        client, "architect", spec={**SPEC, "memories": [{"scope": "global", "slug": "nothing-here"}]}
    )
    assert refused.status_code == 422, refused.text
    assert "unknown memory `nothing-here`" in refused.text
    # ... on a new version too.
    await create_agent(client, "architect")
    again = await client.post(
        f"{ORG}/agents/architect/versions",
        json={**SPEC, "memories": [{"scope": "global", "slug": "nothing-here"}]},
    )
    assert again.status_code == 422 and "unknown memory" in again.text


async def test_an_archived_global_memory_cannot_be_bound(client: AsyncClient, admin: str) -> None:
    await _global_memories(client)
    await client.patch(f"{MEM}/security-lessons", json={"status": "archived"})
    refused = await create_agent(
        client, "architect", spec={**SPEC, "memories": [{"scope": "global", "slug": "security-lessons"}]}
    )
    assert refused.status_code == 422 and "archived" in refused.text


async def test_an_invisible_global_memory_is_unknown_not_forbidden(client: AsyncClient, admin: str) -> None:
    """A publisher who does not see the memory gets the same sentence as for one that does not exist."""
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.errors import ApiError
    from choregos_api.rbac import Principal, Role
    from choregos_api.schemas import AgentSpec
    from choregos_api.services.memory_access import check_bindings_at_publish
    from sqlalchemy import select

    await make_project(client, "billing-api")
    await make_project(client, "checkout-web")
    made = await client.post(
        MEM,
        json={"slug": "billing-only", "display_name": "B", "reach": "projects", "projects": ["billing-api"]},
    )
    assert made.status_code == 201
    spec = AgentSpec.model_validate({"memories": [{"scope": "global", "slug": "billing-only"}]})
    outsider = Principal(user_id="u", email="u@x", project_roles={"varga/checkout-web": Role.DEVELOPER})
    insider = Principal(user_id="v", email="v@x", project_roles={"varga/billing-api": Role.DEVELOPER})
    async with session_scope() as session:
        organisation = (
            await session.execute(select(Organization).where(Organization.slug == "varga"))
        ).scalar_one()
        await check_bindings_at_publish(session, insider, organisation, spec)
        try:
            await check_bindings_at_publish(session, outsider, organisation, spec)
        except ApiError as error:
            assert error.status_code == 422
            assert "unknown memory `billing-only`" in str(error.detail) or "unknown memory" in str(
                error.errors
            )
        else:
            raise AssertionError("an outsider bound a memory it does not see")


async def test_the_binding_rules_are_422(client: AsyncClient, admin: str) -> None:
    await _global_memories(client)

    async def refused(spec: dict[str, Any]) -> str:
        response = await create_agent(client, "architect", spec={**SPEC, **spec})
        assert response.status_code == 422, response.text
        return response.text

    two_writes = [
        {"scope": "project", "slug": "shared", "mode": "write"},
        {"scope": "project", "slug": "own", "mode": "write"},
    ]
    assert "at most one memory may be written" in await refused({"memories": two_writes})
    twice = [{"scope": "project", "slug": "shared"}] * 2
    assert "bound twice" in await refused({"memories": twice})
    nine = [{"scope": "project", "slug": f"memo-{i}"} for i in range(9)]
    await refused({"memories": nine})
    assert "superseded" in await refused(
        {"memory": "agent", "memories": [{"scope": "project", "slug": "shared"}]}
    )
    assert "reserved" in await refused({"memories": [{"scope": "global", "slug": "shared"}]})


async def test_the_legacy_field_derives_the_effective_bindings(client: AsyncClient, admin: str) -> None:
    expected = {
        "agent": [{"scope": "project", "slug": "own", "mode": "write"}],
        "project": [{"scope": "project", "slug": "shared", "mode": "write"}],
        "none": [],
    }
    for memory, bindings in expected.items():
        slug = f"legacy-{memory}"
        created = await create_agent(client, slug, spec={**SPEC, "memory": memory})
        assert created.status_code == 201, created.text
        version = (await client.get(f"{ORG}/agents/{slug}/versions/1")).json()
        assert version["effective_memories"] == bindings, memory
        assert "memories" not in version["spec"], "nothing is rewritten: the stored spec has no memories"


def _old_digest(spec: dict[str, Any]) -> str:
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def test_the_checksum_of_a_version_without_memories_is_unchanged(
    client: AsyncClient, admin: str
) -> None:
    """Computed by hand over the keys that existed before the field: a `memories: null` leaking into the
    digest would move every checksum in the registry."""
    from choregos_api.schemas import AgentSpec
    from choregos_api.services.agents import empreinte

    before = {
        "instructions": "",
        "model": None,
        "backend": None,
        "limits": {"max_turns": None, "max_minutes": None},
        "budget": {"run_usd": None, "daily_usd": None, "monthly_usd": None},
        "skills": [],
        "mcp_servers": [],
        "evals": [],
    }
    assert empreinte(AgentSpec()) == _old_digest(before)
    explicit = AgentSpec.model_validate(
        {"memories": [{"scope": "project", "slug": "shared", "mode": "write"}]}
    )
    assert empreinte(explicit) != _old_digest(before), "an explicit list is a real change"
    created = await create_agent(client, "old-one", spec={"instructions": "Say hello to everyone."})
    assert created.status_code == 201
    stored = (await client.get(f"{ORG}/agents/old-one/versions/1")).json()
    assert stored["checksum"] == _old_digest({**before, "instructions": "Say hello to everyone."})


async def test_a_fork_copies_the_bindings_and_a_trial_validates_them(client: AsyncClient, admin: str) -> None:
    await _global_memories(client)
    await make_project(client, "billing-api")
    await create_agent(client, "architect", spec={**SPEC, "memories": FOUR})
    forked = await client.post(f"{ORG}/agents/architect/fork", json={"slug": "architect-two", "scope": "org"})
    assert forked.status_code == 201, forked.text
    version = (await client.get(f"{ORG}/agents/architect-two/versions/1")).json()
    assert version["spec"]["memories"] == FOUR
    # The trial of a draft that binds an unknown memory is refused before any run starts.
    await as_user(client, "admin@varga.dev")
    trial = await client.post(
        f"{ORG}/agent-trials",
        json={
            "project": "billing-api",
            "spec": {**SPEC, "memories": [{"scope": "global", "slug": "nothing-here"}]},
            "input": {"title": "x"},
        },
    )
    assert trial.status_code == 422 and "unknown memory" in trial.text
    ok = await client.post(
        f"{ORG}/agent-trials",
        json={"project": "billing-api", "spec": {**SPEC, "memories": FOUR}, "input": {"title": "x"}},
    )
    assert ok.status_code == 201, ok.text


async def test_a_project_agent_may_only_bind_its_own_projects_named_memories(
    client: AsyncClient, admin: str
) -> None:
    billing = await make_project(client, "billing-api")
    await make_project(client, "checkout-web")
    await client.post(
        f"/api/v1/projects/{billing['id']}/memories", json={"slug": "decisions", "display_name": "D"}
    )
    bound = [{"scope": "project", "slug": "decisions", "mode": "read"}]
    ok = await create_agent(
        client, "billing-bot", scope="project", project="billing-api", spec={**SPEC, "memories": bound}
    )
    assert ok.status_code == 201, ok.text
    refused = await create_agent(
        client, "checkout-bot", scope="project", project="checkout-web", spec={**SPEC, "memories": bound}
    )
    assert refused.status_code == 422 and "unknown memory `decisions` in project checkout-web" in refused.text
    # An organisation agent's project references are relative: nothing to check at publication.
    assert (await create_agent(client, "org-bot", spec={**SPEC, "memories": bound})).status_code == 201
