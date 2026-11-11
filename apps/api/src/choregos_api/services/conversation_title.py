# SPDX-License-Identifier: Apache-2.0
"""A conversation's title, written by the model from the first exchange (S63-04, ADR 0134 point 5).

The request is one more call on the turn's own key, so its cost is the turn's and is booked with it. Any
failure (a refusal, a spent key, a gateway down, an empty answer) falls back to the first message cut at
`TITLE_CHARS`: a title is never a reason to fail a turn."""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import AgentSessionRecord
from ..db.session import outside_the_transaction
from ..logging import get_logger

log = get_logger(__name__)

TITLE_CHARS = 60
TITLE_TOKENS = 24
#: Named in the request, so a gateway (and a test) can tell it from the turn's own calls.
PURPOSE = "conversation_title"
EXCERPT_CHARS = 1000
INSTRUCTION = (
    "Write a title of at most eight words for the conversation below. Answer with the title only: "
    "no quotes, no final punctuation, in the language of the person."
)


def clean_title(text: str) -> str | None:
    """The first line of the model's answer, without quotes, cut at a word; `None` when nothing is left."""
    lines = [line for line in text.strip().splitlines() if line.strip()]
    line = " ".join(lines[0].split()).strip("\"'`*# ").rstrip(" .,;:") if lines else ""
    if not line:
        return None
    if len(line) <= TITLE_CHARS:
        return line
    return (line[:TITLE_CHARS].rsplit(" ", 1)[0] or line[:TITLE_CHARS]).rstrip(" ,;:.") + "..."


async def model_title(
    session: AsyncSession, record: AgentSessionRecord, key: str, question: str, answer: str
) -> str | None:
    """The title the model gives the first exchange, on the turn's key; `None` when it cannot."""
    from .agent_sessions import _OVERRIDES
    from .gateway import org_gateway_url

    try:
        gateway_url = await org_gateway_url(session, record.org_id) or get_settings().gateway_url
        body: dict[str, Any] = {
            "model": record.model,
            "max_tokens": TITLE_TOKENS,
            "metadata": {"purpose": PURPOSE},
            "messages": [
                {"role": "system", "content": INSTRUCTION},
                {
                    "role": "user",
                    "content": f"Person: {question[:EXCERPT_CHARS]}\n\nAssistant: {answer[:EXCERPT_CHARS]}",
                },
            ],
        }
        async with (
            outside_the_transaction(session),
            httpx.AsyncClient(transport=_OVERRIDES.get("transport"), timeout=30) as http,
        ):
            reply = await http.post(
                f"{gateway_url.rstrip('/')}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {key}"},
            )
        if reply.status_code != 200:
            return None
        message = reply.json()["choices"][0]["message"]
        return clean_title(str(message.get("content") or ""))
    except Exception as failure:  # the cut message stands in
        log.warning("conversation.title_failed", session_id=record.id, error=type(failure).__name__)
        return None
