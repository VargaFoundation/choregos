"""What the offered-knowledge gap tests share (S63-06): a plugin published as a version."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from httpx import AsyncClient
from sqlalchemy import select

from .assistant_install import FIXTURE, OWNERS, _install, _plugin

ORG = "/api/v1/orgs/varga"
BASE = "investment-assistant-investment-policy"
UPDATES = f"{ORG}/assistants/investment-assistant/knowledge/updates"


def publish(
    root: Path,
    version: str,
    upstream: str,
    corpus: dict[str, str],
    sources: list[dict[str, Any]] | None = None,
) -> None:
    """The fixture plugin as `version`: the seed holds `corpus`, follows `upstream`, ships `sources`."""
    target = root / "investment-assistant" / version
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(FIXTURE, target)
    manifest = target / "plugin.yaml"
    manifest.write_text(manifest.read_text().replace("version: 0.1.0", f"version: {version}"))
    assistant = target / "assistants" / "investment-assistant.yaml"
    document = yaml.safe_load(assistant.read_text())
    document["knowledge"][0]["upstream"] = upstream
    assistant.write_text(yaml.safe_dump(document, sort_keys=False))
    seed = target / "knowledge" / "investment-policy"
    if sources is not None:
        knowledge = yaml.safe_load((seed / "knowledge.yaml").read_text())
        knowledge["sources"] = sources
        (seed / "knowledge.yaml").write_text(yaml.safe_dump(knowledge, sort_keys=False))
    shutil.rmtree(seed / "corpus")
    (seed / "corpus").mkdir()
    for name, text in corpus.items():
        (seed / "corpus" / name).write_text(text)


@pytest.fixture
def offline(tmp_path: Path) -> Iterator[Path]:
    from choregos_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    root = tmp_path / "plugins"
    shutil.copytree(FIXTURE, root / "investment-assistant" / "0.1.0")
    use_plugin_supply(OfflineDirectory(root))
    yield root
    use_plugin_supply(None)


async def docs() -> dict[str, tuple[str, str]]:
    """The base's documents: file name to (origin, text)."""
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


async def upgrade(client: AsyncClient, version: str) -> None:
    response = await client.post(f"{ORG}/plugins/investment-assistant/upgrade", json={"version": version})
    assert response.status_code == 200, response.text


async def updates(client: AsyncClient, status: str = "proposed") -> dict[str, dict[str, Any]]:
    response = await client.get(f"{UPDATES}?status={status}")
    assert response.status_code == 200, response.text
    return {u["path"]: u for u in response.json()["items"]}


async def installed(client: AsyncClient) -> None:
    await _plugin(client)
    response = await _install(client, owner_groups=[OWNERS])
    assert response.status_code == 201, response.text
