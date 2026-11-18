# SPDX-License-Identifier: Apache-2.0
"""The frame token of the embedded component (S63-12, ADR 0139 point 6): see `embed_frame`.

It is minted for a person who is signed in with the console's own session (the popup of the embedded
component's sign-in) and who is in the installation's audience: anyone else is answered 404.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

from .. import embed_frame
from ..deps import Config, Db, Me
from ..schemas.assistants import SLUG, EmbedToken
from ..services.assistant_conversations import audience_installation

router = APIRouter(tags=["agents"])

Slug = Annotated[str, Path(pattern=SLUG)]


@router.post(
    "/me/assistants/{org}/{slug}/embed-token", response_model=EmbedToken, operation_id="mintMyEmbedToken"
)
async def mint_my_embed_token(
    org: str, slug: Slug, session: Db, principal: Me, settings: Config
) -> EmbedToken:
    _, row = await audience_installation(session, principal, org, slug)
    token = embed_frame.mint(
        user_id=principal.user_id,
        sid=principal.session_id,
        org=org,
        slug=slug,
        installation_id=row.id,
        settings=settings,
    )
    return EmbedToken(token=token, expires_in=embed_frame.TTL_S)
