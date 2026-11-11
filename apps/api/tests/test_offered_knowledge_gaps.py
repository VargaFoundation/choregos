"""Offered knowledge, the gaps S63-06 left (ADR 0138 points 1-3): a document an owner deleted stays deleted
through an upgrade, a source sync that rewrites a seed document makes it the owners', the seed's own sources
are attached at install, and the owners are told at upgrade time that proposals wait."""

# ruff: noqa: F811 - a fixture imported from a kit is redefined by the parameter that names it

from __future__ import annotations

from pathlib import Path

from choregos_adapters.fakes.notify import FakeNotifier
from httpx import AsyncClient
from sqlalchemy import select

from .agent_scopes_kit import as_user
from .kits.assistant_install import OWNERS, _member
from .kits.offered_knowledge import (  # noqa: F401
    BASE,
    ORG,
    docs,
    installed,
    offline,
    publish,
    updates,
    upgrade,
)

PURPOSE = "00-purpose.md"
V1 = {PURPOSE: "# Purpose\n\nv1 purpose\n", "keep.md": "# Keep\n\nv1 keep\n"}
V2 = {PURPOSE: "# Purpose\n\nv2 purpose\n", "keep.md": "# Keep\n\nv2 keep\n", "new.md": "# New\n\nnew\n"}


async def _delete(client: AsyncClient, filename: str) -> None:
    listed = (await client.get(f"{ORG}/knowledge/{BASE}")).json()["documents"]
    (found,) = [d for d in listed if d["filename"] == filename]
    assert (await client.delete(f"{ORG}/knowledge/{BASE}/documents/{found['id']}")).status_code == 204


async def test_a_seed_document_an_owner_deleted_is_not_added_back_by_follow_nor_proposed(
    client: AsyncClient, org: str, offline: Path
) -> None:
    publish(offline, "0.1.0", "follow", V1)
    await installed(client)
    await _delete(client, "keep.md")
    publish(offline, "0.2.0", "follow", V2)
    await upgrade(client, "0.2.0")
    found = await docs()
    assert "keep.md" not in found, "an upgrade that follows the seed does not add a deleted document back"
    assert found[PURPOSE] == ("seed@0.2.0", V2[PURPOSE]) and found["new.md"][0] == "seed@0.2.0"

    publish(offline, "0.3.0", "propose_updates", {**V2, "keep.md": "# Keep\n\nv3 keep\n"})
    await upgrade(client, "0.3.0")
    assert "keep.md" not in await updates(client), "nor is it proposed again"
    assert "keep.md" not in await docs()


async def test_a_document_the_owners_wrote_is_not_remembered_when_deleted(
    client: AsyncClient, org: str, offline: Path
) -> None:
    publish(offline, "0.1.0", "follow", V1)
    await installed(client)
    written = await client.post(
        f"{ORG}/knowledge/{BASE}/documents",
        files={"file": ("mine.md", b"# Mine\n\nlocal\n", "text/markdown")},
    )
    assert written.status_code in (200, 202), written.text
    await _delete(client, "mine.md")
    publish(offline, "0.2.0", "follow", {**V1, "mine.md": "# Mine\n\nfrom the seed\n"})
    await upgrade(client, "0.2.0")
    assert (await docs())["mine.md"][0] == "seed@0.2.0", "only a document that came from a seed is remembered"


async def test_a_source_sync_that_rewrites_a_seed_document_makes_it_local(
    client: AsyncClient, org: str, offline: Path
) -> None:
    from choregos_adapters.fakes.documents import fake_source
    from choregos_api.db.models import KnowledgeSource
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge.sync import sync_source

    source = {"kind": "git", "name": "house-docs", "config": {"url": "https://git.test/house.git"}}
    publish(offline, "0.1.0", "follow", V1, sources=[source])
    await installed(client)
    assert (await docs())[PURPOSE][0] == "seed@0.1.0"
    async with session_scope(orgs="*") as session:
        source_id = (await session.execute(select(KnowledgeSource.id))).scalar_one()
    site = fake_source(source_id)
    site.put("r1", PURPOSE, b"# Purpose\n\nrewritten by the source\n")
    summary = await sync_source("varga", source_id, adapter=site)
    assert summary["updated"] == 1 and summary["added"] == 0, summary
    found = await docs()
    assert found[PURPOSE] == ("local", "# Purpose\n\nrewritten by the source\n")
    assert sorted(found) == sorted(V1), "one document under that name, not a twin"

    # The seed moved on: the (now local) document is proposed to the owners, never overwritten.
    publish(offline, "0.2.0", "follow", V2, sources=[source])
    await upgrade(client, "0.2.0")
    assert (await docs())[PURPOSE][0] == "local"
    assert PURPOSE in await updates(client)


async def test_the_sources_of_the_seed_are_attached_at_install_and_never_synced_there(
    client: AsyncClient, org: str, offline: Path
) -> None:
    from choregos_api.db.models import KnowledgeBase, KnowledgeSource
    from choregos_api.db.session import session_scope

    source = {
        "kind": "web",
        "name": "house-site",
        "config": {"url": "https://house.test/docs"},
        "interval_minutes": 120,
    }
    ignored = {"kind": "ftp", "name": "nope", "config": {}}
    publish(offline, "0.1.0", "follow", V1, sources=[source, ignored])
    await installed(client)
    async with session_scope(orgs="*") as session:
        rows = (
            await session.execute(
                select(KnowledgeSource, KnowledgeBase.slug).join(
                    KnowledgeBase, KnowledgeBase.id == KnowledgeSource.knowledge_base_id
                )
            )
        ).all()
    ((attached, slug),) = rows
    assert slug == BASE and (attached.kind, attached.name) == ("web", "house-site")
    assert attached.config == {"url": "https://house.test/docs"} and attached.interval_minutes == 120
    assert attached.refresh_mode == "manual" and attached.last_synced_at is None


async def test_the_owners_are_told_at_upgrade_time_that_proposals_wait(
    client: AsyncClient, org: str, offline: Path
) -> None:
    from choregos_api.services.task_delivery import set_notifier_factory

    publish(offline, "0.1.0", "propose_updates", V1)
    await installed(client)
    await _member(client, "owner@varga.dev", [OWNERS])
    await _member(client, "other@varga.dev", ["somewhere-else"])
    await as_user(client, "admin@varga.dev")
    notifier = FakeNotifier()
    set_notifier_factory(lambda row, channel: notifier if channel == "email" else None)
    try:
        publish(offline, "0.2.0", "propose_updates", V2)
        await upgrade(client, "0.2.0")
        assert [to for to, _ in notifier.sent] == ["owner@varga.dev"], "the owners, nobody else"
        message = notifier.sent[0][1]
        assert "knowledge update" in message.title and PURPOSE in message.body and "new.md" in message.body
        notifier.sent.clear()
        publish(offline, "0.3.0", "propose_updates", V2)  # nothing new to propose, nothing to say
        await upgrade(client, "0.3.0")
        assert notifier.sent == []
    finally:
        set_notifier_factory(None)
