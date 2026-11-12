# SPDX-License-Identifier: Apache-2.0
"""A Teams incoming webhook / Workflows URL that records what is posted to it (S63-25).

Scriptable: `fail_with(status)` makes the next posts answer that status, `unreachable` makes them fail as a
dead host does, `cards()` reads what Teams would have shown. Under `CHOREGOS_FAKES=1` the Teams notifier is
built over `transport()`, so the real card, the guarded transport and the egress allowlist are what run.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


class FakeTeamsWebhook:
    def __init__(self) -> None:
        #: (url, parsed JSON body) of every post, in order.
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.status = 202
        self.unreachable = False

    def fail_with(self, status: int) -> None:
        self.status = status

    def cards(self) -> list[dict[str, Any]]:
        """The Adaptive Card of each post."""
        return [post["attachments"][0]["content"] for _, post in self.posts]

    def texts(self) -> list[str]:
        """Every text a card shows, for assertions that do not care about the layout."""
        found: list[str] = []
        for card in self.cards():
            for block in card["body"]:
                if "text" in block:
                    found.append(str(block["text"]))
                found.extend(f"{f['title']}: {f['value']}" for f in block.get("facts", []))
        return found

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.unreachable:
            raise httpx.ConnectError("fake teams is down", request=request)
        self.posts.append((str(request.url), json.loads(request.content)))
        return httpx.Response(self.status, text="")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)
