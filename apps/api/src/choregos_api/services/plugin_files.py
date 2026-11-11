# SPDX-License-Identifier: Apache-2.0
"""The files of an installed plugin, read: its row, its stored files materialised on disk, an assistant's
document, a file it ships.

Apart from `plugin_installer` because it reaches no plugin server: the MCP door and a conversation read an
assistant's manifest through it, and their import closure must not reach an organisation's connectors
(ADR 0105, `tests/licences/test_mcp_gateway_boundary.py`)."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml
from choregos_core.paths import inside
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import OrgPlugin, OrgPluginFile


async def _row(session: AsyncSession, org_id: str, name: str) -> OrgPlugin | None:
    found = await session.execute(select(OrgPlugin).where(OrgPlugin.org_id == org_id, OrgPlugin.name == name))
    return found.scalar_one_or_none()


async def installed_row(session: AsyncSession, org_id: str, name: str) -> OrgPlugin | None:
    return await _row(session, org_id, name)


def _cache_root() -> Path:
    return Path(tempfile.gettempdir()) / "choregos-plugin-files"


async def _materialise(session: AsyncSession, row: OrgPlugin) -> Path:
    """The plugin's stored files on disk, once per content digest (they never change under a digest)."""
    target = _cache_root() / row.org_id / row.digest.removeprefix("sha256:")
    if (target / ".complete").is_file():
        return target
    files = (
        await session.execute(
            select(OrgPluginFile).where(
                OrgPluginFile.org_id == row.org_id, OrgPluginFile.digest == row.digest
            )
        )
    ).scalars()
    _cache_root().mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="staging-", dir=_cache_root()))
    for stored in files:
        path = inside(staging, stored.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(stored.content)
    (staging / ".complete").write_text(row.digest, encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(staging, target)
    except OSError:  # another request materialised it first
        shutil.rmtree(staging, ignore_errors=True)
    return target


async def plugin_root(session: AsyncSession, row: OrgPlugin) -> Path:
    """The directory of the plugin's stored files (see `_materialise`)."""
    return await _materialise(session, row)


async def plugin_assistant(
    session: AsyncSession, org_id: str, plugin: str, name: str
) -> tuple[OrgPlugin, dict[str, Any]] | None:
    """The installed plugin and the document of its assistant `name`, read from the stored files; `None`
    when the plugin is not installed or ships no such assistant (ADR 0133)."""
    row = await _row(session, org_id, plugin)
    if row is None:
        return None
    root = await _materialise(session, row)
    for ref in (row.manifest.get("contents") or {}).get("assistants") or []:
        path = inside(root, str(ref))
        if not path.is_file():
            continue
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(document, dict) and (document.get("metadata") or {}).get("name") == name:
            return row, document
    return None


async def plugin_text(session: AsyncSession, org_id: str, plugin: str, ref: str) -> str | None:
    """The text of a file the installed plugin ships (a task's workflow, ADR 0137); `None` when the plugin
    is not installed or ships no such file."""
    row = await _row(session, org_id, plugin)
    if row is None:
        return None
    path = inside(await _materialise(session, row), ref)
    return path.read_text(encoding="utf-8") if path.is_file() else None
