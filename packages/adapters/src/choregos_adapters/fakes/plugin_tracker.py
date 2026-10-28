# SPDX-License-Identifier: Apache-2.0
"""A plugin server that serves the tracker profile, in memory (S43-07, ADR 0005).

`FakePluginTrackerServer` answers MCP over HTTP through an `httpx.MockTransport`: `initialize`,
`tools/list` (the eight profile tools) and `tools/call`. It keeps items, notes and states the way the
profile says a real server does: a replayed `action_key` writes nothing, an upsert with the same text
adds nothing, closing a closed item answers `closed: false`, and `tracker_parse_event` verifies the
`X-Choregos-Secret` header against its secret and answers `{kind, item_key, delivery_id}` per event,
`delivery_id = <key>:<revision>` so a replayed delivery has the same id.

It also records every request (`requests`), so a test can see the bearer token on each call, and lets a
test script an answer (`answers[tool]`) — an item that is not what the payload said, a malformed
answer, a refusal.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

CLOSED = {"Resolved", "Closed"}


@dataclass
class FakeItem:
    key: str
    title: str
    body: str = ""
    state: str = "New"
    revision: int = 0
    notes: list[dict[str, Any]] = field(default_factory=list)


class FakePluginTrackerServer:
    """In-memory tracker profile server. `secret`: what `X-Choregos-Secret` must carry."""

    def __init__(self, secret: str = "fake-webhook-secret", *, token: str | None = None) -> None:
        self.secret = secret
        #: When set, a call without `Authorization: Bearer <token>` is a 401 (what a server must do).
        self.token = token
        self.items: dict[str, FakeItem] = {}
        self.requests: list[httpx.Request] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.answers: dict[str, dict[str, Any]] = {}
        self._keys: dict[str, str] = {}

    # -- scripting ---------------------------------------------------------------------------------

    def add(self, key: str, title: str, body: str = "", state: str = "New") -> FakeItem:
        self.items[key] = FakeItem(key, title, body, state)
        return self.items[key]

    def delivery(self, key: str, operation: str = "update") -> bytes:
        """The body the system would post for a change of `key` (its revision bumped)."""
        item = self.items[key]
        item.revision += 1
        return json.dumps({"number": key, "operation": operation, "revision": item.revision}).encode()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # -- MCP ---------------------------------------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.token is not None and request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401)
        body = json.loads(request.content or b"{}")
        method = body.get("method")
        if method == "initialize":
            result: dict[str, Any] = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fake-tracker"}}
        elif method == "tools/list":
            from choregos_core.plugins import TRACKER_PROFILE

            result = {"tools": [{"name": t, "inputSchema": {"type": "object"}} for t in TRACKER_PROFILE]}
        elif method == "tools/call":
            params = body.get("params") or {}
            result = self._call(str(params.get("name")), dict(params.get("arguments") or {}))
        else:
            return httpx.Response(202)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"), "result": result})

    def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, args))
        if tool in self.answers:
            return self.answers[tool]
        handler = getattr(self, f"_{tool}", None)
        if handler is None:
            return _refused(f"`{tool}` is not served")
        try:
            answer: dict[str, Any] = handler(args)
        except KeyError as missing:
            return _refused(f"unknown item {missing}")
        return {"structuredContent": answer, "content": [{"type": "text", "text": json.dumps(answer)}]}

    # -- the profile -------------------------------------------------------------------------------

    def _tracker_fetch_item(self, args: dict[str, Any]) -> dict[str, Any]:
        item = self.items[args["key"]]
        return {
            "key": item.key,
            "title": item.title,
            "body": item.body,
            "url": f"https://desk.example.com/{item.key}",
            "state": item.state,
            "labels": [],
            "item_type": "incident",
            "assignees": [],
            "author": None,
            "created_at": None,
            "updated_at": None,
            "closed_at": None,
            "fields": {"revision": item.revision},
            "comments": [
                {"id": str(i), "author": "choregos", "body": n["body"], "ts": None, "marker": n["marker"]}
                for i, n in enumerate(item.notes)
            ],
        }

    def _tracker_list_candidates(self, args: dict[str, Any]) -> dict[str, Any]:
        keys = [k for k, item in self.items.items() if item.state not in CLOSED]
        return {"keys": keys[: int(args.get("limit") or 50)]}

    def _tracker_parse_event(self, args: dict[str, Any]) -> dict[str, Any]:
        headers = {str(k).lower(): str(v) for k, v in (args.get("headers") or {}).items()}
        if not hmac.compare_digest(headers.get("x-choregos-secret", ""), self.secret):
            return {"verified": False, "events": []}
        raw = json.loads(args.get("body") or "{}")
        events = []
        for entry in raw if isinstance(raw, list) else [raw]:
            kind = {"insert": "tracker.item.created", "comment": "tracker.item.commented"}.get(
                str(entry.get("operation")), "tracker.item.updated"
            )
            key = str(entry.get("number"))
            events.append({"kind": kind, "item_key": key, "delivery_id": f"{key}:{entry.get('revision')}"})
        return {"verified": True, "events": events}

    def _tracker_create_item(self, args: dict[str, Any]) -> dict[str, Any]:
        known = self._keys.get(args["action_key"])
        if known is not None:
            return {"key": known, "created": False}
        key = f"INC{len(self.items) + 1:07d}"
        self.add(key, args["title"], args.get("body") or "")
        self._keys[args["action_key"]] = key
        return {"key": key, "created": True}

    def _tracker_set_state(self, args: dict[str, Any]) -> dict[str, Any]:
        item = self.items[args["key"]]
        status = args.get("status")
        ignored = [n for n in ("label", "remove_labels") if args.get(n)]
        if not status or status == item.state:
            return {"key": item.key, "changed": False, "state": item.state, "ignored": ignored}
        item.state = str(status)
        return {"key": item.key, "changed": True, "state": item.state, "ignored": ignored}

    def _tracker_comment(self, args: dict[str, Any]) -> dict[str, Any]:
        item = self.items[args["key"]]
        tag = f"[choregos:{args['action_key']}]"
        if any(n["marker"] == tag for n in item.notes):
            return {"key": item.key, "id": tag, "written": False}
        item.notes.append({"body": args["markdown"], "marker": tag})
        return {"key": item.key, "id": tag, "written": True}

    def _tracker_upsert_status(self, args: dict[str, Any]) -> dict[str, Any]:
        item = self.items[args["key"]]
        tag = f"[choregos-status:{args['marker']}]"
        latest = [n for n in item.notes if n["marker"] == tag]
        if latest and latest[-1]["body"] == args["markdown"]:
            return {"key": item.key, "written": False}
        item.notes.append({"body": args["markdown"], "marker": tag})
        return {"key": item.key, "written": True}

    def _tracker_close(self, args: dict[str, Any]) -> dict[str, Any]:
        item = self.items[args["key"]]
        if item.state in CLOSED:
            return {"key": item.key, "closed": False, "state": item.state}
        item.state = "Resolved"
        return {"key": item.key, "closed": True, "state": item.state}


def _refused(text: str) -> dict[str, Any]:
    return {"isError": True, "content": [{"type": "text", "text": f"refused: {text}"}]}


__all__ = ["FakeItem", "FakePluginTrackerServer"]
