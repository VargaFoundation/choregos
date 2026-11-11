# SPDX-License-Identifier: Apache-2.0
"""A person's standing delegations (S63-14, ADR 0137 points 3-4): agree, list, revoke. Always the caller's
own: another person's delegation is a 404."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from ..deps import Db, Me
from ..schemas.delegations import Delegation, DelegationCreate, DelegationList
from ..services import assistant_delegations

router = APIRouter(tags=["agents"])


@router.post(
    "/me/delegations",
    response_model=Delegation,
    status_code=status.HTTP_201_CREATED,
    operation_id="createMyDelegation",
)
async def create_delegation(body: DelegationCreate, session: Db, principal: Me) -> Delegation:
    return await assistant_delegations.grant(session, principal, body)


@router.get("/me/delegations", response_model=DelegationList, operation_id="listMyDelegations")
async def list_delegations(session: Db, principal: Me) -> DelegationList:
    return DelegationList(items=await assistant_delegations.mine(session, principal))


@router.delete(
    "/me/delegations/{delegation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="revokeMyDelegation",
)
async def revoke_delegation(delegation_id: str, session: Db, principal: Me) -> Response:
    await assistant_delegations.revoke(session, principal, delegation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
