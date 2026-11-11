# SPDX-License-Identifier: Apache-2.0
"""A manifest's assistant, summarised for the console (S63-18): read-only views, no decision made here."""

from __future__ import annotations

from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas.assistant_catalogue import (
    AssistantMemorySpace,
    AssistantNeed,
    AssistantTaskDecl,
    PluginAssistant,
)
from . import plugin_installer


def _mappings(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def needs_of(document: dict[str, Any]) -> list[AssistantNeed]:
    return [
        AssistantNeed(
            need=str(t["need"]),
            identity=t.get("identity") or "service",
            access=str(t.get("access") or "read"),
            required=bool(t.get("required", False)),
        )
        for t in _mappings(document.get("tools"))
        if t.get("need")
    ]


def memory_spaces_of(document: dict[str, Any], *, owners: bool = False) -> list[AssistantMemorySpace]:
    """A person's spaces; with `owners`, the installation's too (never on a person's page)."""
    return [
        AssistantMemorySpace(
            name=str(m["name"]), scope=m.get("scope") or "user", kind=m.get("kind") or "facts"
        )
        for m in _mappings(document.get("memory"))
        if m.get("name") and (owners or m.get("scope") != "installation")
    ]


def tasks_of(document: dict[str, Any]) -> list[AssistantTaskDecl]:
    return [
        AssistantTaskDecl(
            name=str(t["name"]),
            delegation=t.get("delegation") or "none",
            schedulable=any(x.get("kind") in ("schedule", "poll") for x in _mappings(t.get("triggers"))),
        )
        for t in _mappings(document.get("tasks"))
        if t.get("name")
    ]


async def plugin_assistants(session: AsyncSession, org_id: str, plugin: str) -> list[PluginAssistant] | None:
    """The assistants an installed plugin ships; `None` when the plugin is not installed."""
    row = await plugin_installer.installed_row(session, org_id, plugin)
    if row is None:
        return None
    found: list[PluginAssistant] = []
    for ref in ((row.manifest or {}).get("contents") or {}).get("assistants") or []:
        text = await plugin_installer.plugin_text(session, org_id, plugin, str(ref))
        document = yaml.safe_load(text) if text else None
        if not isinstance(document, dict):
            continue
        metadata = document.get("metadata") or {}
        found.append(
            PluginAssistant(
                name=str(metadata.get("name", "")),
                display_name=metadata.get("display_name"),
                description=metadata.get("description"),
                config_schema=document.get("config_schema"),
                suggested_groups=[
                    str(g) for g in (document.get("audience") or {}).get("suggested_groups") or []
                ],
                needs=needs_of(document),
                memory_spaces=memory_spaces_of(document, owners=True),
            )
        )
    return found
