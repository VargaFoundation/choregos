# SPDX-License-Identifier: Apache-2.0
"""`GET /public/assistants/{org}/{slug}/embed`: who may frame the embedded component (S63-12, ADR 0139).

Public on purpose: the console answers the embed page with `frame-ancestors` set to these origins, and it
must do so for a visitor who is not signed in yet (the page then shows its sign-in). It carries origins only:
no name, no status, no configuration. A 404 for an installation that does not exist.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

from ..deps import PlatformDb
from ..schemas.assistants import SLUG, AssistantEmbed
from ..services.assistant_embed import public_embed_origins

router = APIRouter(tags=["agents"])


@router.get(
    "/public/assistants/{org}/{slug}/embed",
    response_model=AssistantEmbed,
    operation_id="getAssistantEmbed",
)
async def get_assistant_embed(
    org: str, slug: Annotated[str, Path(pattern=SLUG)], session: PlatformDb
) -> AssistantEmbed:
    return AssistantEmbed(embed_origins=await public_embed_origins(session, org, slug))
