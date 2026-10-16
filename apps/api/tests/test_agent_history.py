"""An agent's history (S40-12a, ADR 0076): versions carry a message, publish from a base, restore as new."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .agent_scopes_kit import (
    ADMIN,
    ORG,
    SPEC,
    add_member,
    as_user,
    create_agent,
    install_common,
    make_project,
)

DEV = "dev@varga.dev"
OWNER = "owner@varga.dev"
VIP = {"name": "vip", "input": {"title": "VIP outage"}, "must_contain": ["Escalate VIP outage"]}


def _spec(text: str, **extra: Any) -> dict[str, Any]:
    return {**SPEC, "instructions": text, **extra}


async def _publish(client: AsyncClient, slug: str, text: str, base: int, message: str = "A change") -> Any:
    return await client.post(
        f"{ORG}/agents/{slug}/versions/publish",
        json={"spec": _spec(text), "message": message, "base_version": base},
    )


async def _agent(client: AsyncClient, slug: str = "helper") -> dict[str, Any]:
    await as_user(client, ADMIN)
    made = await create_agent(client, slug)
    assert made.status_code == 201, made.text
    return dict(made.json())


async def test_a_published_version_carries_its_message_author_and_date(client: AsyncClient, org: str) -> None:
    await _agent(client)
    published = await _publish(client, "helper", "Second.", 1, "  Tighten the wording  ")
    assert published.status_code == 201, published.text
    body = published.json()
    assert body["version"] == 2 and body["message"] == "Tighten the wording"
    assert body["created_by"] == ADMIN and body["created_at"] and body["restored_from"] is None
    versions = (await client.get(f"{ORG}/agents/helper")).json()["versions"]
    assert [(v["version"], v["message"]) for v in versions] == [(2, "Tighten the wording"), (1, "Created")]
    assert (await client.get(f"{ORG}/agents/helper/versions/2")).json()["message"] == "Tighten the wording"


async def test_publishing_from_a_stale_base_is_refused_with_the_latest(client: AsyncClient, org: str) -> None:
    await _agent(client)
    assert (await _publish(client, "helper", "Second.", 1)).status_code == 201
    stale = await _publish(client, "helper", "Mine.", 1)
    assert stale.status_code == 409, stale.text
    problem = stale.json()
    assert (problem["code"], problem["latest_version"], problem["latest_author"]) == ("stale_base", 2, ADMIN)
    assert (await client.get(f"{ORG}/agents/helper")).json()["latest_version"] == 2, "nothing was created"
    assert (await _publish(client, "helper", "Mine.", 2)).status_code == 201


async def test_publishing_an_unchanged_spec_is_refused_but_the_old_endpoint_still_creates_it(
    client: AsyncClient, org: str
) -> None:
    await _agent(client)
    same = await client.post(
        f"{ORG}/agents/helper/versions/publish", json={"spec": SPEC, "message": "No-op", "base_version": 1}
    )
    assert same.status_code == 409 and same.json()["code"] == "no_change", same.text
    assert same.json()["latest_version"] == 1
    # compatibility: the original endpoint takes a bare spec, with no message, and may repeat it
    old = await client.post(f"{ORG}/agents/helper/versions", json=SPEC)
    assert old.status_code == 201, old.text
    assert old.json()["version"] == 2 and old.json()["message"] is None


async def test_a_message_is_required_and_bounded(client: AsyncClient, org: str) -> None:
    await _agent(client)
    for message in ("", "   ", "x" * 501):
        refused = await _publish(client, "helper", "Second.", 1, message)
        assert refused.status_code == 422, (message, refused.text)
    missing = await client.post(
        f"{ORG}/agents/helper/versions/publish", json={"spec": SPEC, "base_version": 1}
    )
    assert missing.status_code == 422
    assert (await client.get(f"{ORG}/agents/helper")).json()["latest_version"] == 1


async def test_restore_appends_a_copy_with_the_same_checksum(client: AsyncClient, org: str) -> None:
    await _agent(client)
    await _publish(client, "helper", "Second.", 1)
    await _publish(client, "helper", "Third.", 2)
    before = (await client.get(f"{ORG}/agents/helper")).json()["versions"]
    restored = await client.post(f"{ORG}/agents/helper/versions/1/restore", json={"base_version": 3})
    assert restored.status_code == 201, restored.text
    body = restored.json()
    first = next(v for v in before if v["version"] == 1)
    assert body["version"] == 4 and body["restored_from"] == 1 and body["message"] == "Restore v1"
    assert body["checksum"] == first["checksum"] and body["spec"] == first["spec"]
    after = (await client.get(f"{ORG}/agents/helper")).json()["versions"]
    assert [(v["version"], v["spec"], v["checksum"]) for v in after[1:]] == [
        (v["version"], v["spec"], v["checksum"]) for v in before
    ], "versions 1 to 3 are untouched"
    custom = await client.post(
        f"{ORG}/agents/helper/versions/2/restore", json={"base_version": 4, "message": "Back to the wording"}
    )
    assert custom.json()["message"] == "Back to the wording" and custom.json()["restored_from"] == 2


async def test_restore_respects_governance_and_the_eval_gate(client: AsyncClient, org: str) -> None:
    await add_member(client, DEV, "developer")
    await as_user(client, ADMIN)
    await install_common(client)
    await as_user(client, DEV)
    refused = await client.post(f"{ORG}/agents/developer/versions/1/restore", json={"base_version": 1})
    assert refused.status_code == 403 and refused.json()["detail"], refused.text

    await as_user(client, ADMIN)
    first = await client.post(
        f"{ORG}/agents", json={"slug": "triage", "display_name": "Triage", "spec": _spec("Sort the ticket.")}
    )
    assert first.status_code == 201, first.text
    gated = await client.post(
        f"{ORG}/agents/triage/versions/publish",
        json={
            "spec": _spec("Escalate {{ ticket.title }}.", evals=[VIP]),
            "message": "Add the VIP case",
            "base_version": 1,
        },
    )
    assert gated.status_code == 201, gated.text
    # v1 fails the case v2 published: restoring it is refused, naming the case
    blocked = await client.post(f"{ORG}/agents/triage/versions/1/restore", json={"base_version": 2})
    assert blocked.status_code == 409, blocked.text
    problem = blocked.json()
    assert problem["code"] == "eval_failed" and problem["errors"][0]["loc"] == ["evals", "vip"]
    assert (await client.get(f"{ORG}/agents/triage")).json()["latest_version"] == 2


async def test_restore_of_an_unknown_version_is_404(client: AsyncClient, org: str) -> None:
    await _agent(client)
    missing = await client.post(f"{ORG}/agents/helper/versions/9/restore", json={"base_version": 1})
    assert missing.status_code == 404


async def test_restore_of_the_current_spec_is_no_change_and_a_stale_base_is_refused(
    client: AsyncClient, org: str
) -> None:
    await _agent(client)
    await _publish(client, "helper", "Second.", 1)
    same = await client.post(f"{ORG}/agents/helper/versions/2/restore", json={"base_version": 2})
    assert same.status_code == 409 and same.json()["code"] == "no_change"
    stale = await client.post(f"{ORG}/agents/helper/versions/1/restore", json={"base_version": 1})
    assert stale.status_code == 409 and stale.json()["code"] == "stale_base"


async def test_pinned_by_lists_the_projects_the_caller_may_see(client: AsyncClient, org: str) -> None:
    from choregos_api.db.models import Agent
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import Principal, Role
    from choregos_api.routers.agents import _pins

    dev = await make_project(client, "dev")
    ops = await make_project(client, "ops")
    await _agent(client)
    await _publish(client, "helper", "Second.", 1)
    assert (
        await client.put(f"/api/v1/projects/{dev['id']}/agents/helper", json={"version": 1})
    ).status_code == 200
    assert (
        await client.put(f"/api/v1/projects/{ops['id']}/agents/helper", json={"version": 1})
    ).status_code == 200
    admin_view = (await client.get(f"{ORG}/agents/helper")).json()["versions"]
    assert {v["version"]: v["pinned_by"] for v in admin_view} == {2: [], 1: ["dev", "ops"]}
    assert (await client.get(f"{ORG}/agents/helper/versions/1")).json()["pinned_by"] == ["dev", "ops"]

    # A member of one project only (the dev sign-in gives an organisation role, so the principal is built):
    # the other project's pin stays out of the answer.
    member = Principal(user_id="u", email="m@varga.dev", project_roles={"varga/dev": Role.DEVELOPER})
    async with session_scope(orgs="*") as session:
        from sqlalchemy import select

        agent = (await session.execute(select(Agent).where(Agent.slug == "helper"))).scalar_one()
        assert await _pins(session, member, "varga", agent) == {1: ["dev"]}  # type: ignore[arg-type]


async def test_platform_written_versions_have_a_message(
    client: AsyncClient, org: str, monkeypatch: Any
) -> None:
    import dataclasses

    from choregos_core import catalogue_d_agents
    from choregos_core.catalogue_d_agents import entree, entrees

    await as_user(client, ADMIN)
    created = await create_agent(client, "plain", message="  The first draft ")
    assert created.json()["versions"][0]["message"] == "The first draft"
    installed = await client.post(f"{ORG}/agent-catalogue/triager/install")
    assert installed.json()["versions"][0]["message"] == "Installed from the catalogue (triager@1)"
    forked = await client.post(f"{ORG}/agents/triager/fork", json={"slug": "triager-copy", "scope": "org"})
    assert forked.json()["versions"][0]["message"] == "Forked from triager@1"
    explicit = await client.post(
        f"{ORG}/agents/triager/fork", json={"slug": "triager-two", "scope": "org", "message": "For billing"}
    )
    assert explicit.json()["versions"][0]["message"] == "For billing"

    old = entree("triager")
    assert old is not None
    document = {**old.document, "spec": {**old.document["spec"], "instructions": "Triage it, briefly."}}
    new = dataclasses.replace(old, version=2, document=document)
    others = tuple(e for e in entrees() if e.slug != "triager")
    monkeypatch.setattr(catalogue_d_agents, "entrees", lambda: (*others, new))
    upgraded = await client.post(f"{ORG}/agent-catalogue/triager/install", json={"upgrade": True})
    assert upgraded.json()["versions"][0]["message"] == "Catalogue update to triager@2"


async def test_restore_copies_the_stored_bytes_not_a_re_serialisation(client: AsyncClient, org: str) -> None:
    """A version written before a field existed has a stored spec and checksum a re-serialisation would
    change: the restore keeps both as they were."""
    from choregos_api.db.models import AgentVersion
    from choregos_api.db.session import session_scope
    from sqlalchemy import update

    await _agent(client)
    await _publish(client, "helper", "Second.", 1)
    legacy = {"instructions": "Legacy text."}
    async with session_scope(orgs="*") as session:
        await session.execute(
            update(AgentVersion)
            .where(AgentVersion.version == 1)
            .values(spec=legacy, checksum="sha256:written-long-ago")
        )
    restored = await client.post(f"{ORG}/agents/helper/versions/1/restore", json={"base_version": 2})
    assert restored.status_code == 201, restored.text
    assert restored.json()["checksum"] == "sha256:written-long-ago"
    async with session_scope(orgs="*") as session:
        from sqlalchemy import select

        row = (await session.execute(select(AgentVersion).where(AgentVersion.version == 3))).scalar_one()
        assert row.spec == legacy and row.restored_from == 1
