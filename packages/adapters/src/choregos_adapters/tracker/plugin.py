# SPDX-License-Identifier: Apache-2.0
"""`tracker/plugin`: a plugin's MCP server is a project's tracker (plan S43 Decision 3, S43-07).

A server that declares `implements: [tracker]` serves the eight tools of the tracker profile
(`docs/plugins.md#the-tracker-profile`, `plugin-tracker-profile.schema.json`). This adapter maps
`TrackerAdapter` onto them, through the platform's client of that server (`PluginServerClient`: the
Service URL the platform wrote, the server's own bearer token, no redirect).

These are **platform calls**, not agent tools: they do not go through an agent's tool selection nor
through a governed action, exactly like the platform's writes to Jira or GitHub. The workflow already
decided them (a state mirrored, a status comment, a delivered item closed after its review stage); an
approval per status comment would put a person in front of every transition for nothing. They are
audited instead: each write calls `audit(tool, details)` (the API records it in the organisation's
audit trail, in its own transaction, since the write has happened whatever the caller's transaction
does next).

Every answer is checked against the profile's output schema, and is third-party data: a webhook's
events give only a kind, a key and a delivery id, and the item is re-read with `tracker_fetch_item`
before anything acts on it (`choregos_api.routers.webhooks.plugin_webhook`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from functools import cache
from typing import Any, Literal, Protocol

import structlog
from choregos_contracts import InboundEvent, ProjectConfig, load_schema
from choregos_core.domain import Comment, NewItem, TrackerStateMapping, WorkItemData
from choregos_core.plugins import TRACKER_PROFILE
from jsonschema import Draft202012Validator

from ..errors import UpstreamError

log = structlog.get_logger(__name__)

PROFILE_SCHEMA = "plugin-tracker-profile.schema.json"
#: What `close_item` writes as the close notes: the delivered item's review already happened.
CLOSE_NOTES = "Closed by Choregos: the work item was delivered."

Audit = Callable[[str, dict[str, Any]], Awaitable[None]]


class ToolClient(Protocol):
    """What the adapter needs of the server's MCP client (`PluginServerClient`)."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


@cache
def _output_validator(tool: str) -> Draft202012Validator:
    profile = load_schema(PROFILE_SCHEMA)
    return Draft202012Validator({"$defs": profile["$defs"], "$ref": f"#/$defs/{tool}_output"})


def action_key(tool: str, *parts: str) -> str:
    """The idempotency key of a platform write: the same call replayed (a Temporal retry) is the same
    key, so the server answers the first write instead of writing twice."""
    digest = hashlib.sha256("\x1f".join((tool, *parts)).encode()).hexdigest()[:32]
    return f"platform:{digest}"


class PluginTracker:
    """A plugin server that serves the tracker profile, as a `TrackerAdapter`."""

    #: The items live in the server's system (ServiceNow…): Choregos reflects them.
    owns_items = True

    def __init__(self, client: ToolClient, plugin: str, server: str, *, audit: Audit | None = None) -> None:
        self.client = client
        self.plugin = plugin
        self.server = server
        self._audit = audit

    @property
    def service(self) -> str:
        return f"plugin:{self.plugin}/{self.server}"

    # -- the call ------------------------------------------------------------------------------------

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await self.client.call_tool(tool, arguments)
        except UpstreamError:
            raise
        except Exception as exc:  # the MCP client's own errors (HTTP, protocol)
            raise UpstreamError(self.service, f"`{tool}`: {exc}") from exc
        if result.get("isError"):
            raise UpstreamError(self.service, f"`{tool}` refused: {_text(result)[:500]}")
        answer = result.get("structuredContent")
        if not isinstance(answer, dict):
            try:
                answer = json.loads(_text(result))
            except ValueError:
                answer = None
        if not isinstance(answer, dict):
            raise UpstreamError(self.service, f"`{tool}` answered no JSON object")
        problems = [e.message for e in _output_validator(tool).iter_errors(answer)]
        if problems:
            raise UpstreamError(
                self.service, f"`{tool}` answered outside the tracker profile: {'; '.join(problems[:3])}"
            )
        return answer

    async def _write(self, tool: str, arguments: dict[str, Any], details: dict[str, Any]) -> dict[str, Any]:
        answer = await self._call(tool, arguments)
        log.info("tracker.plugin.write", plugin=self.plugin, server=self.server, tool=tool, **details)
        if self._audit is not None:
            try:
                await self._audit(tool, {**details, "plugin": self.plugin, "server": self.server})
            except Exception as exc:  # the write happened: a failed audit entry is said, not raised
                log.error("tracker.plugin.audit_failed", tool=tool, error=str(exc))
        return answer

    # -- reads ---------------------------------------------------------------------------------------

    async def fetch_item(self, key: str) -> WorkItemData:
        found = await self._call("tracker_fetch_item", {"key": key})
        if found["key"] != key:
            raise UpstreamError(self.service, f"`tracker_fetch_item` answered `{found['key']}` for `{key}`")
        comments = [
            Comment(
                id=c.get("id"),
                author=c.get("author") or "",
                body=c["body"],
                marker=c.get("marker"),
                **({"ts": c["ts"]} if c.get("ts") else {}),
            )
            for c in found.get("comments") or []
        ]
        return WorkItemData(
            key=key,
            title=found["title"],
            body=found.get("body") or "",
            url=found.get("url"),
            state=found.get("state"),
            labels=list(found.get("labels") or []),
            item_type=found.get("item_type"),
            assignees=list(found.get("assignees") or []),
            author=found.get("author"),
            created_at=found.get("created_at"),
            updated_at=found.get("updated_at"),
            closed_at=found.get("closed_at"),
            fields=dict(found.get("fields") or {}),
            comments=comments,
        )

    async def list_candidates(self, project: ProjectConfig) -> list[str]:
        found = await self._call("tracker_list_candidates", {"limit": 50})
        return [str(k) for k in found["keys"]]

    async def parse_event(self, headers: Mapping[str, str], body: bytes) -> tuple[bool, list[dict[str, Any]]]:
        """A webhook delivery, verified and normalised by the server: `(verified, events)`, each event
        `{kind, item_key, delivery_id}` and nothing else trusted."""
        found = await self._call(
            "tracker_parse_event",
            {"headers": dict(headers), "body": body.decode("utf-8", errors="replace")},
        )
        events = [
            {"kind": e["kind"], "item_key": str(e["item_key"]), "delivery_id": str(e["delivery_id"])}
            for e in found["events"]
        ]
        return bool(found["verified"]), events if found["verified"] else []

    # -- writes --------------------------------------------------------------------------------------

    async def create_item(self, data: NewItem) -> str:
        arguments = {
            "title": data.title,
            "body": data.body,
            "labels": list(data.labels),
            "assignees": list(data.assignees),
            "fields": dict(data.fields),
            "action_key": action_key("tracker_create_item", data.title, data.body),
        }
        created = await self._write("tracker_create_item", arguments, {"title": data.title[:200]})
        return str(created["key"])

    async def set_state(self, key: str, state_mapping: TrackerStateMapping) -> None:
        arguments = {
            "key": key,
            "status": state_mapping.status,
            "label": state_mapping.label,
            "remove_labels": list(state_mapping.remove_labels),
        }
        await self._write("tracker_set_state", arguments, {"key": key, "status": state_mapping.status})

    async def upsert_status_comment(self, key: str, markdown: str, marker: str) -> None:
        await self._write(
            "tracker_upsert_status",
            {"key": key, "markdown": markdown, "marker": _marker(marker)},
            {"key": key, "marker": _marker(marker)},
        )

    async def close_item(self, key: str) -> None:
        await self._write(
            "tracker_close", {"key": key, "close_notes": CLOSE_NOTES, "close_code": None}, {"key": key}
        )

    async def comment(self, key: str, markdown: str) -> str:
        written = await self._write(
            "tracker_comment",
            {"key": key, "markdown": markdown, "action_key": action_key("tracker_comment", key, markdown)},
            {"key": key},
        )
        return str(written["id"])

    async def link(self, a: str, b: str, relation: Literal["origin", "duplicate", "blocks"]) -> None:
        """Not in the profile: the server's system may have no links. Said, not failed."""
        log.info("tracker.plugin.link_skipped", plugin=self.plugin, server=self.server, a=a, b=b)

    async def set_fields(self, key: str, fields: dict[str, Any]) -> None:
        """Not in the profile (the cost, size and risk fields Choregos writes to Jira): the status
        comment carries them. A field write is an agent's governed operation of the server."""
        log.debug("tracker.plugin.fields_skipped", plugin=self.plugin, server=self.server, key=key)

    # -- what the protocol asks of a tracker that verifies its own webhooks ---------------------------

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        """A plugin tracker's deliveries are verified by its server (`parse_event`, async), at
        `POST /webhooks/plugins/{plugin}/{server}`: nothing is parsed here."""
        return []

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        """Only the server holds the delivery secret: nothing verifies here."""
        return False

    async def test(self) -> dict[str, Any]:
        """The server answers and serves the whole tracker profile."""
        lister = getattr(self.client, "list_tools", None)
        if lister is None:
            return {"ok": True}
        served = {t.name for t in await lister()}
        missing = sorted(set(TRACKER_PROFILE) - served)
        return {"ok": not missing, "server": self.service, **({"missing": missing} if missing else {})}


def _marker(marker: str) -> str:
    """The profile's marker alphabet (`[A-Za-z0-9._:-]{1,80}`): Choregos's markers are HTML comments
    (`<!-- choregos:status -->`), reduced to their name."""
    bare = marker.strip().removeprefix("<!--").removesuffix("-->").strip()
    cleaned = "".join(ch for ch in bare if ch.isascii() and (ch.isalnum() or ch in "._:-"))
    return (cleaned or "status")[:80]


def _text(result: dict[str, Any]) -> str:
    return "".join(
        str(part.get("text", "")) for part in result.get("content") or [] if isinstance(part, dict)
    )


__all__ = ["CLOSE_NOTES", "PROFILE_SCHEMA", "PluginTracker", "action_key"]
