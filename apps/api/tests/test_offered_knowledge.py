# SPDX-License-Identifier: Apache-2.0
"""The knowledge an assistant offers (S63-06, ADR 0138 points 1-3).

Installing copies each seed into a base of the installation with `origin: seed@<version>`; an owner's edit
makes a document `local`; upgrading the plugin compares the seed the copy came from, the new seed and the
copy, per `upstream`; the owners accept, merge or refuse what is proposed, and the index follows.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from httpx import AsyncClient
from sqlalchemy import select

from .agent_scopes_kit import add_member, as_user
from .kits.assistant_install import FIXTURE, OWNERS, _install, _member, _plugin

ORG = "/api/v1/orgs/varga"
BASE = "investment-assistant-investment-policy"
UPDATES = f"{ORG}/assistants/investment-assistant/knowledge/updates"


def _publish(root: Path, version: str, upstream: str, corpus: dict[str, str]) -> None:
    """The fixture plugin as `version`, whose seed holds exactly `corpus` and follows `upstream`."""
    target = root / "investment-assistant" / version
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(FIXTURE, target)
    manifest = target / "plugin.yaml"
    manifest.write_text(manifest.read_text().replace("version: 0.1.0", f"version: {version}"))
    assistant = target / "assistants" / "investment-assistant.yaml"
    document = yaml.safe_load(assistant.read_text())
    document["knowledge"][0]["upstream"] = upstream
    assistant.write_text(yaml.safe_dump(document, sort_keys=False))
    corpus_dir = target / "knowledge" / "investment-policy" / "corpus"
    shutil.rmtree(corpus_dir)
    corpus_dir.mkdir()
    for name, text in corpus.items():
        (corpus_dir / name).write_text(text)


@pytest.fixture
def offline(tmp_path: Path) -> Iterator[Path]:
    from choregos_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    root = tmp_path / "plugins"
    shutil.copytree(FIXTURE, root / "investment-assistant" / "0.1.0")
    use_plugin_supply(OfflineDirectory(root))
    yield root
    use_plugin_supply(None)


async def _docs() -> dict[str, tuple[str, str]]:
    from choregos_api.db.models import KnowledgeBase, KnowledgeDocument
    from choregos_api.db.session import session_scope
    from sqlalchemy.orm import undefer

    async with session_scope(orgs="*") as session:
        rows = (
            await session.execute(
                select(KnowledgeDocument)
                .options(undefer(KnowledgeDocument.content))
                .join(KnowledgeBase, KnowledgeBase.id == KnowledgeDocument.knowledge_base_id)
                .where(KnowledgeBase.slug == BASE)
            )
        ).scalars()
        return {d.filename: (d.origin, (d.content or b"").decode()) for d in rows}


async def _chunks(filename: str) -> str:
    from choregos_api.db.models import KnowledgeChunk, KnowledgeDocument
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        found = await session.execute(
            select(KnowledgeChunk.body)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(KnowledgeDocument.filename == filename)
        )
        return "\n".join(found.scalars())


async def _edit(client: AsyncClient, name: str, text: str) -> None:
    response = await client.post(
        f"{ORG}/knowledge/{BASE}/documents", files={"file": (name, text.encode(), "text/markdown")}
    )
    assert response.status_code in (200, 202), response.text


async def _upgrade(client: AsyncClient, version: str) -> None:
    response = await client.post(f"{ORG}/plugins/investment-assistant/upgrade", json={"version": version})
    assert response.status_code == 200, response.text


async def _updates(client: AsyncClient, status: str = "proposed") -> dict[str, dict[str, Any]]:
    response = await client.get(f"{UPDATES}?status={status}")
    assert response.status_code == 200, response.text
    return {u["path"]: u for u in response.json()["items"]}


async def _decide(client: AsyncClient, update: dict[str, Any], **body: Any) -> Any:
    return await client.post(f"{UPDATES}/{update['id']}", json=body)


async def _installed(client: AsyncClient) -> None:
    await _plugin(client)
    response = await _install(client, owner_groups=[OWNERS])
    assert response.status_code == 201, response.text


async def test_the_base_is_created_at_install_from_the_seed(
    client: AsyncClient, org: str, offline: Path
) -> None:
    from choregos_api.db.models import AssistantInstallation, KnowledgeBase, KnowledgeGrant
    from choregos_api.db.session import session_scope

    await _installed(client)
    docs = await _docs()
    assert list(docs) == ["00-purpose.md"]
    assert docs["00-purpose.md"][0] == "seed@0.1.0"
    # serving_mode index: the document is indexed
    assert "never recommends buying or selling" in await _chunks("00-purpose.md")
    async with session_scope(orgs="*") as session:
        base = (await session.execute(select(KnowledgeBase).where(KnowledgeBase.slug == BASE))).scalar_one()
        installation = (await session.execute(select(AssistantInstallation))).scalar_one()
        assert base.installation_id == installation.id and base.serving_mode == "index"
        grants = (await session.execute(select(KnowledgeGrant))).scalars().all()
        assert [(g.subject_type, g.subject, g.role) for g in grants] == [("group", OWNERS, "owner")]


async def test_an_owner_edits_and_the_document_becomes_local(
    client: AsyncClient, org: str, offline: Path
) -> None:
    await _installed(client)
    await _member(client, "owner@varga.dev", [OWNERS])
    await as_user(client, "owner@varga.dev")
    await _edit(client, "00-purpose.md", "# Purpose\n\nEdited by the house.\n")
    await _edit(client, "01-extra.md", "# Extra\n\nAdded by the house.\n")
    docs = await _docs()
    assert docs["00-purpose.md"] == ("local", "# Purpose\n\nEdited by the house.\n")
    assert docs["01-extra.md"][0] == "local"
    assert len(docs) == 2  # an edit, not a second document


V1 = {n: f"# {n}\n\nv1 of {n}\n" for n in ("unchanged.md", "localedit.md", "upedit.md", "both.md", "gone.md")}
V2 = {
    **{n: V1[n] for n in ("unchanged.md", "localedit.md")},
    "upedit.md": "# upedit.md\n\nv2 of upedit.md\n",
    "both.md": "# both.md\n\nv2 of both.md, upstream\n",
    "new.md": "# new.md\n\nbrand new\n",
}
LOCAL_EDIT = "# localedit.md\n\nlocal v1\n"
LOCAL_BOTH = "# both.md\n\nlocal both\n"


async def _scenario(client: AsyncClient, offline: Path, upstream: str) -> dict[str, tuple[str, str]]:
    _publish(offline, "0.1.0", "propose_updates", V1)
    await _installed(client)
    await _edit(client, "localedit.md", LOCAL_EDIT)
    await _edit(client, "both.md", LOCAL_BOTH)
    _publish(offline, "0.2.0", upstream, V2)
    await _upgrade(client, "0.2.0")
    return await _docs()


async def test_follow_updates_what_nobody_changed_and_proposes_the_rest(
    client: AsyncClient, org: str, offline: Path
) -> None:
    docs = await _scenario(client, offline, "follow")
    assert docs["unchanged.md"] == ("seed@0.1.0", V1["unchanged.md"])
    assert docs["upedit.md"] == ("seed@0.2.0", V2["upedit.md"])  # unchanged locally, changed upstream
    assert docs["localedit.md"] == ("local", LOCAL_EDIT)  # changed locally only
    assert docs["both.md"] == ("local", LOCAL_BOTH)  # changed on both sides: never overwritten
    assert docs["new.md"] == ("seed@0.2.0", V2["new.md"])
    assert "gone.md" in docs  # never silently deleted
    proposed = await _updates(client)
    assert {p: u["kind"] for p, u in proposed.items()} == {"both.md": "update", "gone.md": "remove"}
    assert "-local both" in proposed["both.md"]["diff"]
    assert "+v2 of both.md, upstream" in proposed["both.md"]["diff"]
    assert "v2 of upedit" in await _chunks("upedit.md")  # the index followed


async def test_propose_updates_proposes_every_upstream_change(
    client: AsyncClient, org: str, offline: Path
) -> None:
    docs = await _scenario(client, offline, "propose_updates")
    assert docs["upedit.md"] == ("seed@0.1.0", V1["upedit.md"])
    assert docs["localedit.md"] == ("local", LOCAL_EDIT)
    assert "new.md" not in docs and "gone.md" in docs
    proposed = await _updates(client)
    assert {p: u["kind"] for p, u in proposed.items()} == {
        "upedit.md": "update",
        "both.md": "update",
        "gone.md": "remove",
        "new.md": "add",
    }


async def test_frozen_changes_nothing_but_a_document_changed_on_both_sides(
    client: AsyncClient, org: str, offline: Path
) -> None:
    docs = await _scenario(client, offline, "frozen")
    assert docs["upedit.md"] == ("seed@0.1.0", V1["upedit.md"])
    assert docs["localedit.md"] == ("local", LOCAL_EDIT)
    assert "new.md" not in docs and "gone.md" in docs
    assert {p: u["kind"] for p, u in (await _updates(client)).items()} == {"both.md": "update"}


async def test_accepting_updates_the_document_and_its_index_merging_keeps_the_owners_text(
    client: AsyncClient, org: str, offline: Path
) -> None:
    await _scenario(client, offline, "propose_updates")
    proposed = await _updates(client)
    accepted = await _decide(client, proposed["upedit.md"], decision="accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    assert (await _docs())["upedit.md"] == ("seed@0.2.0", V2["upedit.md"])
    chunks = await _chunks("upedit.md")
    assert "v2 of upedit" in chunks and "v1 of upedit" not in chunks

    merged = await _decide(
        client, proposed["both.md"], decision="merge", content="# both.md\n\nmerged text\n"
    )
    assert merged.status_code == 200 and merged.json()["status"] == "merged"
    assert (await _docs())["both.md"] == ("local", "# both.md\n\nmerged text\n")
    assert "merged text" in await _chunks("both.md")

    added = await _decide(client, proposed["new.md"], decision="accept")
    assert added.status_code == 200
    assert (await _docs())["new.md"] == ("seed@0.2.0", V2["new.md"])

    refused = await _decide(client, proposed["gone.md"], decision="refuse")
    assert refused.json()["status"] == "refused" and "gone.md" in await _docs()
    assert (await _decide(client, proposed["gone.md"], decision="accept")).status_code == 409
    # A refusal is remembered: the same upstream state is not proposed again by the next upgrade.
    _publish(offline, "0.3.0", "propose_updates", V2)
    await _upgrade(client, "0.3.0")
    assert "gone.md" not in await _updates(client)


async def test_accepting_a_removal_deletes_the_document(client: AsyncClient, org: str, offline: Path) -> None:
    await _scenario(client, offline, "propose_updates")
    proposed = await _updates(client)
    assert (await _decide(client, proposed["gone.md"], decision="accept")).status_code == 200
    assert "gone.md" not in await _docs()
    assert await _chunks("gone.md") == ""


async def test_only_the_owners_read_and_decide(client: AsyncClient, org: str, offline: Path) -> None:
    await _scenario(client, offline, "propose_updates")
    proposed = await _updates(client)
    await add_member(client, "plain@varga.dev", "developer")
    await as_user(client, "plain@varga.dev")
    assert (await client.get(UPDATES)).status_code == 403
    assert (await _decide(client, proposed["both.md"], decision="accept")).status_code == 403
    await _member(client, "owner@varga.dev", [OWNERS])
    await as_user(client, "owner@varga.dev")
    assert (await client.get(UPDATES)).status_code == 200
    assert (await _decide(client, proposed["both.md"], decision="refuse")).status_code == 200
