# SPDX-License-Identifier: Apache-2.0
"""A knowledge need bound to the organisation's bases, by reference (S66-12, ADR 0159).

A required binding need left unbound keeps the installation `incomplete`; binding it makes it `ready`
without copying anything; the binder must read every base they add (a base they may not read and one that
does not exist get the same 422), and keeping or removing one needs no right on it; the install body binds
at once; a plugin upgrade that drops, adds or makes a need required follows the need-change path of S63-22.
"""

# ruff: noqa: F811 - the fixtures imported from the kits are redefined by the parameters that name them

from __future__ import annotations

import copy
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_adapters.fakes.notify import FakeNotifier
from httpx import AsyncClient

from .agent_scopes_kit import as_user
from .kits.assistant_install import (
    FIXTURE,
    MARKET,
    NEWS,
    ORG,
    OWNERS,
    _bind,
    _connector,
    _install,
    _kinds,
    _member,
    _plugin,
    offline,  # noqa: F401
)

SLUG = f"{ORG}/assistants/investment-assistant"
MANIFEST = Path("assistants") / "investment-assistant.yaml"
NEED = {
    "name": "house_research",
    "bind": {"description": "The house's own research notes.", "required": True},
}


def _need(**changes: Any) -> dict[str, Any]:
    """A fresh copy of the need: a test that edits one must not edit the next test's."""
    return {**copy.deepcopy(NEED), **changes}


@pytest.fixture
def told() -> Iterator[FakeNotifier]:
    """The owners' notices of an upgrade (the installation's email notifier)."""
    from choregos_api.services.task_delivery import set_notifier_factory

    notifier = FakeNotifier()
    set_notifier_factory(lambda row, channel: notifier if channel == "email" else None)
    yield notifier
    set_notifier_factory(None)


def _knowledge(root: Path, change: Callable[[list[dict[str, Any]]], None]) -> None:
    path = root / MANIFEST
    document = yaml.safe_load(path.read_text())
    change(document["knowledge"])
    path.write_text(yaml.safe_dump(document, sort_keys=False))


def _with_need(offline: Path) -> None:
    _knowledge(offline / "investment-assistant" / "0.1.0", lambda items: items.append(_need()))


def _publish(offline: Path, version: str, change: Callable[[list[dict[str, Any]]], None]) -> None:
    target = offline / "investment-assistant" / version
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(offline / "investment-assistant" / "0.1.0", target)
    manifest = target / "plugin.yaml"
    manifest.write_text(manifest.read_text().replace("version: 0.1.0", f"version: {version}"))
    _knowledge(target, change)


async def _base(slug: str, *, restricted: bool = False, status: str = "active") -> str:
    from choregos_api.db.models import KnowledgeBase, Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "varga"))
        ).scalar_one()
        base = KnowledgeBase(
            org_id=org_id, slug=slug, name=slug, scope="org", serving_mode="index", restricted=restricted,
            status=status,
        )  # fmt: skip
        session.add(base)
        await session.flush()
        return base.id


async def _bases() -> dict[str, Any]:
    from choregos_api.db.models import KnowledgeBase
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        return {b.slug: b.installation_id for b in (await session.execute(select(KnowledgeBase))).scalars()}


async def _bind_knowledge(client: AsyncClient, bases: list[str], name: str = "house_research") -> Any:
    return await client.put(f"{SLUG}/knowledge-bindings/{name}", json={"bases": bases})


async def _tools_bound(client: AsyncClient) -> None:
    await _connector("news-feed", NEWS)
    await _connector("markets", MARKET)
    assert (await _bind(client, "news", "news-feed")).status_code == 200
    assert (await _bind(client, "market_data", "markets")).status_code == 200


async def test_a_required_knowledge_need_unbound_is_incomplete_and_binding_it_copies_nothing(
    client: AsyncClient, org: str, offline: Path
) -> None:
    _with_need(offline)
    await _plugin(client)
    assert (await _install(client)).status_code == 201
    await _tools_bound(client)
    assert await _kinds(client) == [("knowledge_unbound", "house_research")]
    shipped = (await client.get(f"{ORG}/plugins/investment-assistant/assistants")).json()["items"][0]
    assert shipped["knowledge_needs"] == [
        {"name": "house_research", "description": "The house's own research notes.", "required": True}
    ]
    await _base("house-notes")
    before = await _bases()

    bound = await _bind_knowledge(client, ["house-notes"])
    assert bound.status_code == 200, bound.text
    assert bound.json()["status"] == "ready"
    assert bound.json()["knowledge_bindings"] == [
        {"name": "house_research", "bases": ["house-notes"], "enabled": True}
    ]
    # By reference: no base is created, the bound base stays the organisation's, linked to no installation.
    assert await _bases() == before and before["house-notes"] is None
    again = await _bind_knowledge(client, ["house-notes"])  # idempotent
    assert again.json()["knowledge_bindings"] == bound.json()["knowledge_bindings"]

    unbound = await _bind_knowledge(client, [])
    assert unbound.json()["status"] == "incomplete" and unbound.json()["knowledge_bindings"] == []
    assert await _kinds(client) == [("knowledge_unbound", "house_research")]
    assert (await _bind_knowledge(client, ["house-notes"], name="investment_policy")).status_code == 404


async def test_an_optional_knowledge_need_left_unbound_is_not_a_gap(
    client: AsyncClient, org: str, offline: Path
) -> None:
    _knowledge(
        offline / "investment-assistant" / "0.1.0",
        lambda items: items.append(_need(bind={**NEED["bind"], "required": False})),
    )
    await _plugin(client)
    await _install(client)
    await _tools_bound(client)
    assert (await client.get(f"{SLUG}/readiness")).json() == {"status": "ready", "items": []}


async def test_the_binder_reads_what_they_bind_and_a_missing_base_answers_alike(
    client: AsyncClient, org: str, offline: Path
) -> None:
    from choregos_api.db.models import KnowledgeGrant, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    _with_need(offline)
    await _plugin(client)
    await _install(client, owner_groups=[OWNERS])
    secret = await _base("secret-notes", restricted=True)
    await _base("old-notes", status="archived")
    await _base("house-notes")
    await _member(client, "owner@varga.dev", [OWNERS])
    await as_user(client, "owner@varga.dev")

    hidden = await _bind_knowledge(client, ["secret-notes"])
    missing = await _bind_knowledge(client, ["no-such-notes"])
    assert hidden.status_code == missing.status_code == 422, hidden.text
    assert "`secret-notes` is not a knowledge base of varga you may read" in hidden.text
    assert hidden.text.replace("secret-notes", "X") == missing.text.replace("no-such-notes", "X")
    archived = await _bind_knowledge(client, ["old-notes"])
    assert archived.status_code == 422 and "`old-notes` is archived" in archived.text
    # One refusal and nothing changes, even for the base the owner could bind.
    assert (await _bind_knowledge(client, ["house-notes", "secret-notes"])).status_code == 422
    assert (await client.get(SLUG)).json()["knowledge_bindings"] == []

    async with session_scope(orgs="*") as session:
        owner = (await session.execute(select(User).where(User.email == "owner@varga.dev"))).scalar_one()
        org_id = (await session.execute(select(Organization.id))).scalar_one()
        session.add(
            KnowledgeGrant(
                org_id=org_id, knowledge_base_id=secret, subject_type="user", subject=owner.id, role="viewer"
            )
        )
    assert (await _bind_knowledge(client, ["secret-notes"])).status_code == 200


async def test_keeping_or_removing_a_base_needs_no_right_on_it(
    client: AsyncClient, org: str, offline: Path
) -> None:
    _with_need(offline)
    await _plugin(client)
    await _install(client, owner_groups=[OWNERS])
    await _base("secret-notes", restricted=True)
    await _base("house-notes")
    assert (await _bind_knowledge(client, ["secret-notes"])).status_code == 200  # the administrator reads it
    await _member(client, "owner@varga.dev", [OWNERS])
    await as_user(client, "owner@varga.dev")
    kept = await _bind_knowledge(client, ["secret-notes", "house-notes"])
    assert kept.status_code == 200, kept.text
    assert kept.json()["knowledge_bindings"][0]["bases"] == ["house-notes", "secret-notes"]
    removed = await _bind_knowledge(client, ["house-notes"])
    assert removed.json()["knowledge_bindings"][0]["bases"] == ["house-notes"]
    # The audience's members who are not owners do not bind.
    await _member(client, "someone@varga.dev", ["grp-investors"])
    await as_user(client, "someone@varga.dev")
    assert (await _bind_knowledge(client, [])).status_code == 403


async def test_the_install_body_binds_at_once_under_the_same_rules(
    client: AsyncClient, org: str, offline: Path
) -> None:
    _with_need(offline)
    await _plugin(client)
    await _base("house-notes")
    refused = await _install(client, knowledge_bindings={"house_research": ["no-such-notes"]})
    assert refused.status_code == 422, refused.text
    assert (await client.get(f"{ORG}/assistants")).json()["items"] == []
    installed = await _install(client, knowledge_bindings={"house_research": ["house-notes"]})
    assert installed.status_code == 201, installed.text
    assert installed.json()["knowledge_bindings"] == [
        {"name": "house_research", "bases": ["house-notes"], "enabled": True}
    ]
    assert ("knowledge_unbound", "house_research") not in await _kinds(client)


async def test_an_upgrade_that_drops_and_adds_knowledge_needs_follows_the_need_change_path(
    client: AsyncClient, org: str, offline: Path, told: FakeNotifier
) -> None:
    _with_need(offline)
    await _plugin(client)
    assert (await _install(client, owner_groups=[OWNERS])).status_code == 201
    await _member(client, "owner@varga.dev", [OWNERS])
    await as_user(client, "admin@varga.dev")
    await _tools_bound(client)
    await _base("house-notes")
    assert (await _bind_knowledge(client, ["house-notes"])).json()["status"] == "ready"

    def rename(items: list[dict[str, Any]]) -> None:  # a rename is a drop and an add
        items[:] = [i for i in items if i["name"] != "house_research"] + [_need(name="house_notes")]

    _publish(offline, "0.2.0", rename)
    upgraded = await client.post(f"{ORG}/plugins/investment-assistant/upgrade", json={"version": "0.2.0"})
    assert upgraded.status_code == 200, upgraded.text
    after = (await client.get(SLUG)).json()
    assert after["knowledge_bindings"] == [
        {"name": "house_research", "bases": ["house-notes"], "enabled": False}
    ]
    assert await _kinds(client) == [("knowledge_unbound", "house_notes")]
    body = told.sent[-1][1].body
    assert "the knowledge `house_research` is no longer a need" in body
    assert "the knowledge `house_notes` is a new required need" in body

    def optional(items: list[dict[str, Any]]) -> None:
        rename(items)
        items[-1]["bind"]["required"] = False

    _publish(offline, "0.3.0", optional)
    upgraded = await client.post(f"{ORG}/plugins/investment-assistant/upgrade", json={"version": "0.3.0"})
    assert upgraded.status_code == 200, upgraded.text
    assert (await client.get(f"{SLUG}/readiness")).json()["status"] == "ready"
    assert "the knowledge `house_notes` is now optional" in told.sent[-1][1].body


async def test_a_need_that_comes_back_is_bound_again_by_its_owners(
    client: AsyncClient, org: str, offline: Path, told: FakeNotifier
) -> None:
    _with_need(offline)
    await _plugin(client)
    await _install(client)
    await _tools_bound(client)
    await _base("house-notes")
    await _bind_knowledge(client, ["house-notes"])
    _publish(offline, "0.2.0", lambda items: items.pop())
    assert (
        await client.post(f"{ORG}/plugins/investment-assistant/upgrade", json={"version": "0.2.0"})
    ).status_code == 200
    _publish(offline, "0.3.0", lambda items: None)  # the copy of 0.1.0 still declares the need
    assert (
        await client.post(f"{ORG}/plugins/investment-assistant/upgrade", json={"version": "0.3.0"})
    ).status_code == 200
    assert await _kinds(client) == [("knowledge_unbound", "house_research")]
    again = await _bind_knowledge(client, ["house-notes"])
    assert again.json()["status"] == "ready"
    assert again.json()["knowledge_bindings"][0]["enabled"] is True
    assert FIXTURE.is_dir()  # the shared fixture is never edited
