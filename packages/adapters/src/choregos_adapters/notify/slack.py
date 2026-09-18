"""Notifier Slack : messages avec boutons qui renvoient vers l'API de décision (§3.1)."""

from __future__ import annotations

from typing import Any

import httpx
from choregos_core.domain import Message

from ..errors import UpstreamError

SEVERITY_EMOJI = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "🔴"}


class SlackNotifier:
    """Envoi par webhook entrant (simple) ou par `chat.postMessage` (boutons)."""

    def __init__(
        self,
        *,
        webhook_url: str = "",
        bot_token: str = "",
        default_channel: str = "#choregos",
        public_url: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.bot_token = bot_token
        self.default_channel = default_channel
        self.public_url = public_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=15.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send(self, channel: str, message: Message) -> None:
        blocks = self._blocks(message)
        payload: dict[str, Any] = {"text": message.title, "blocks": blocks}
        if self.bot_token:
            response = await self._client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {self.bot_token}"},
                json={**payload, "channel": channel or self.default_channel},
            )
            body = response.json() if response.content else {}
            if not body.get("ok", False):
                raise UpstreamError("slack", f"chat.postMessage : {body.get('error', response.text[:200])}")
            return
        if not self.webhook_url:
            raise UpstreamError("slack", "ni `bot_token` ni `webhook_url` configurés")
        response = await self._client.post(self.webhook_url, json=payload)
        if response.status_code >= 400:
            raise UpstreamError("slack", f"webhook → {response.status_code} : {response.text[:200]}")

    def _blocks(self, message: Message) -> list[dict[str, Any]]:
        emoji = SEVERITY_EMOJI.get(message.severity, "ℹ️")
        blocks: list[dict[str, Any]] = [
            {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} {message.title}"[:150]}}
        ]
        if message.body:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": message.body[:2900]}})
        if message.context:
            fields = [
                {"type": "mrkdwn", "text": f"*{key}*\n{value}"}
                for key, value in list(message.context.items())[:8]
            ]
            blocks.append({"type": "section", "fields": fields})
        elements: list[dict[str, Any]] = []
        for action in message.actions:
            elements.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": action.label},
                    "style": {"primary": "primary", "danger": "danger"}.get(action.style),
                    "action_id": f"choregos:{action.id}",
                    "value": action.value,
                }
            )
        if message.url:
            elements.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Ouvrir dans Choregos"},
                    "url": message.url,
                    "action_id": "choregos:open",
                }
            )
        if elements:
            blocks.append({"type": "actions", "elements": [_clean(e) for e in elements]})
        return blocks

    async def test(self) -> dict[str, Any]:
        if self.bot_token:
            response = await self._client.post(
                "https://slack.com/api/auth.test", headers={"Authorization": f"Bearer {self.bot_token}"}
            )
            body = response.json() if response.content else {}
            return {"ok": bool(body.get("ok")), "team": body.get("team")}
        return {"ok": bool(self.webhook_url), "mode": "webhook"}


def _clean(element: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in element.items() if value is not None}
