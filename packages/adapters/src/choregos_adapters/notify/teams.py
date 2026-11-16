# SPDX-License-Identifier: Apache-2.0
"""Microsoft Teams notifier: an Adaptive Card posted to an incoming webhook or a Workflows URL (S63-25).

The URL IS the credential (whoever holds it can post to the channel): it comes from a sealed secret, is never
written in a log, an error or a repr, and the call goes through the guarded transport, so the organisation's
egress allowlist decides whether its host may be reached. The `channel` of `send` is ignored: a webhook is
bound to one channel, which its creator chose in Teams.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx
from choregos_core.domain import Message

from ..errors import AdapterError, UpstreamError
from ..http.egress import guarded

#: Teams refuses a message over about 28 KB: the card keeps well under it.
MAX_BODY = 4000
MAX_FACTS = 8
SEVERITY_COLOUR = {"info": "default", "success": "good", "warning": "warning", "error": "attention"}
CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"
CARD_TYPE = "application/vnd.microsoft.card.adaptive"


def check_webhook_url(url: str) -> str:
    """An `https` URL with a host and no credentials in it; the text of a refusal never repeats the URL."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise AdapterError("a Teams webhook is an `https` URL with a host and no credentials in it")
    return url.strip()


def card(message: Message) -> dict[str, Any]:
    """The message as the body Teams takes: one Adaptive Card attachment."""
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": message.title[:300],
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
            "color": SEVERITY_COLOUR.get(message.severity, "default"),
        }
    ]
    if message.body:
        body.append({"type": "TextBlock", "text": message.body[:MAX_BODY], "wrap": True})
    if message.context:
        facts = [
            {"title": str(k)[:60], "value": str(v)[:300]}
            for k, v in list(message.context.items())[:MAX_FACTS]
        ]
        body.append({"type": "FactSet", "facts": facts})
    content: dict[str, Any] = {"$schema": CARD_SCHEMA, "type": "AdaptiveCard", "version": "1.4", "body": body}
    if message.url:
        content["actions"] = [{"type": "Action.OpenUrl", "title": "Open in Choregos", "url": message.url}]
    return {
        "type": "message",
        "attachments": [{"contentType": CARD_TYPE, "contentUrl": None, "content": content}],
    }


class TeamsNotifier:
    """Posts a message card to a Teams incoming webhook or Workflows URL."""

    channel_kind = "teams"

    def __init__(
        self,
        *,
        webhook_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self._url = check_webhook_url(webhook_url)
        self._client = httpx.AsyncClient(
            transport=guarded(transport), timeout=timeout_s, follow_redirects=False
        )

    def __repr__(self) -> str:
        return "TeamsNotifier(webhook_url=<sealed>)"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send(self, channel: str, message: Message) -> None:
        try:
            response = await self._client.post(self._url, json=card(message))
        except AdapterError:
            raise  # an egress refusal: its text names the host and the policy, never the path
        except httpx.HTTPError as exc:
            # The exception may carry the request, whose URL is the secret: only its type is kept.
            raise UpstreamError("teams", f"the webhook is unreachable ({type(exc).__name__})") from None
        if not 200 <= response.status_code < 300:
            raise UpstreamError("teams", f"webhook answered {response.status_code}")

    async def test(self) -> dict[str, Any]:
        return {"ok": True, "mode": "webhook"}
