"""Atlassian's Rovo over A2A, for real (S39-08, ADR 0062).

`packages/adapters/tests/test_a2a_client.py` proves the client against a scriptable fake and the real
card kept as a fixture. This file proves the real gateway accepts it: the card still parses, a message
is answered, the task reaches a final state.

Prerequisites: A2A allowed in Atlassian Administration, and an ACCESS token of the dedicated account
(the one a `connected` credential gives a run: it lives about an hour):

    export CHOREGOS_LIVE_ROVO_ACCESS_TOKEN=...        # from the OAuth consent, never committed
    export CHOREGOS_LIVE_ROVO_PROMPT="List the 3 most recent issues of project KEY"   # optional
    uv run pytest tests/live/test_rovo_a2a_live.py -m live
"""

from __future__ import annotations

import asyncio
import os

import pytest
from choregos_adapters.a2a import A2aClient, fetch_card, text_part

pytestmark = [pytest.mark.live, pytest.mark.asyncio]

CARD = "https://a2a.atlassian.com/.well-known/agent.json"
TOKEN = os.environ.get("CHOREGOS_LIVE_ROVO_ACCESS_TOKEN", "")
needs_token = pytest.mark.skipif(not TOKEN, reason="CHOREGOS_LIVE_ROVO_ACCESS_TOKEN absent: no live Rovo")


async def test_the_live_card_still_parses() -> None:
    card = await fetch_card(CARD)
    assert {s.id for s in card.skills} >= {"work-in-jira", "work-in-confluence"}
    assert card.auth_types == ("oauth2_authorization_code",) and card.dcr_url


@needs_token
async def test_a_message_reaches_a_final_state() -> None:
    card = await fetch_card(CARD)
    prompt = os.environ.get("CHOREGOS_LIVE_ROVO_PROMPT", "List the 3 most recent Jira issues I can see.")
    async with A2aClient(card.url, TOKEN, card.protocol_version) as client:
        task = await client.send(client.message([text_part(prompt)]))
        for _ in range(60):
            if task.terminal or task.interrupted:
                break
            await asyncio.sleep(5)
            task = await client.get_task(task.id)
    assert task.state == "completed", f"{task.state}: {task.message[:200]}"
    assert task.message or task.artifacts
