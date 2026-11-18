# SPDX-License-Identifier: Apache-2.0
"""What waits for the caller (ADR 0154 §4, G8): one list for every client, terminal or editor."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ..deps import Db, Me
from ..schemas.pending import PendingList
from ..services.my_pending import pending_for

router = APIRouter(tags=["agents"])


@router.get("/me/pending", response_model=PendingList, operation_id="listMyPending")
async def list_my_pending(
    session: Db, principal: Me, limit: Annotated[int, Query(ge=1, le=200)] = 100
) -> PendingList:
    """What waits for the caller: steps, governed actions, proposed writes and launches; newest first."""
    return PendingList(items=await pending_for(session, principal, limit=limit))
