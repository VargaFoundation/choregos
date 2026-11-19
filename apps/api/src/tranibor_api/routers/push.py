# SPDX-License-Identifier: Apache-2.0
"""Web Push: the instance's key, and the caller's devices that are told (S81-03, ADR 0194)."""

from __future__ import annotations

from typing import get_args

from fastapi import APIRouter, Response, status
from sqlalchemy import delete, select
from tranibor_core import utcnow

from ..audit import record
from ..db.models import PushSubscription
from ..db.session import ALL_ORGS, session_scope
from ..decision_session import holds_a_person_session, refusal
from ..deps import Config, Db, Me
from ..errors import ApiError, conflict, not_found, unprocessable
from ..push import sender as senders
from ..push.delivery import GONE, attempt, record_outcome
from ..push.endpoints import EndpointRefusedError, check_endpoint, host_of
from ..push.sender import Target
from ..push.webpush import PushCryptoError, check_subscription_keys
from ..schemas.push import (
    PushKind,
    PushSubscriptionCreateDto,
    PushSubscriptionDto,
    PushTestResultDto,
    PushVapidKeyDto,
)

router = APIRouter(tags=["session"])

NOT_CONFIGURED = "push_not_configured"
ALL_KINDS: list[PushKind] = list(get_args(PushKind))


def _dto(row: PushSubscription) -> PushSubscriptionDto:
    return PushSubscriptionDto(
        id=row.id,
        device_name=row.device_name,
        endpoint_host=host_of(row.endpoint),
        kinds=ALL_KINDS if row.kinds is None else [k for k in ALL_KINDS if k in row.kinds],
        created_at=row.created_at,
        last_delivered_at=row.last_delivered_at,
        last_failure_at=row.last_failure_at,
    )


def _not_configured(code: int) -> ApiError:
    detail = "this instance has no VAPID key: no device can be told (CHOREGOS_PUSH_VAPID_PRIVATE_KEY)"
    if code == status.HTTP_404_NOT_FOUND:
        return ApiError(code, "Push is not configured", detail, extra={"code": NOT_CONFIGURED})
    return conflict(detail, extra={"code": NOT_CONFIGURED})


@router.get("/push/vapid-public-key", response_model=PushVapidKeyDto, operation_id="getPushVapidPublicKey")
async def get_vapid_public_key(_principal: Me, settings: Config) -> PushVapidKeyDto:
    """What a device subscribes with. Signed-in callers only: the key says nothing secret, but the
    route is the phone's first call after its sign-in."""
    key = senders.public_key(settings)
    if key is None:
        raise _not_configured(status.HTTP_404_NOT_FOUND)
    return PushVapidKeyDto(public_key=key)


@router.get(
    "/me/push-subscriptions", response_model=list[PushSubscriptionDto], operation_id="listMyPushSubscriptions"
)
async def list_my_push_subscriptions(session: Db, principal: Me) -> list[PushSubscriptionDto]:
    rows = (
        await session.execute(
            select(PushSubscription)
            .where(PushSubscription.user_id == principal.user_id)
            .order_by(PushSubscription.created_at.desc(), PushSubscription.id)
        )
    ).scalars()
    return [_dto(row) for row in rows]


@router.post(
    "/me/push-subscriptions", response_model=PushSubscriptionDto, operation_id="createMyPushSubscription"
)
async def create_my_push_subscription(
    body: PushSubscriptionCreateDto, response: Response, principal: Me, settings: Config
) -> PushSubscriptionDto:
    """Register a device, or refresh one: the same endpoint is the same device (200), a new one is 201.
    An endpoint another person had registered moves to the caller: whoever holds the device now is the
    person it should tell, and the endpoint is a secret only that device knows."""
    if not holds_a_person_session(principal):
        raise refusal("registering a device")
    if not senders.configured(settings):
        raise _not_configured(status.HTTP_409_CONFLICT)
    try:
        await check_endpoint(body.endpoint, allow_private=settings.push_allow_private_endpoints)
        check_subscription_keys(body.keys.p256dh, body.keys.auth)
    except (EndpointRefusedError, PushCryptoError) as error:
        raise unprocessable(str(error)) from error
    kinds: list[str] | None = None if body.kinds is None else sorted(set(body.kinds))
    # Platform scope: the endpoint is unique across people, and the row another person owns is
    # invisible to the caller. The owner written is always the caller's.
    async with session_scope(orgs=ALL_ORGS) as scoped:
        row = (
            await scoped.execute(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
        ).scalar_one_or_none()
        created = row is None
        if row is None:
            row = PushSubscription(endpoint=body.endpoint, user_id=principal.user_id)
            scoped.add(row)
        row.user_id = principal.user_id
        row.p256dh, row.auth = body.keys.p256dh, body.keys.auth
        row.device_name, row.kinds = body.device_name, kinds
        row.failing_since = None
        await scoped.flush()
        await scoped.refresh(row)
        dto = _dto(row)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return dto


@router.delete(
    "/me/push-subscriptions/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteMyPushSubscription",
)
async def delete_my_push_subscription(id: str, session: Db, principal: Me) -> Response:
    done = await session.execute(
        delete(PushSubscription).where(
            PushSubscription.id == id, PushSubscription.user_id == principal.user_id
        )
    )
    if done.rowcount != 1:  # type: ignore[attr-defined]
        raise not_found("Push subscription", id)
    await record(
        session, principal, "push.unregister", org_id=None, target_type="push_subscription", target_id=id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/me/push-subscriptions/{id}/test",
    response_model=PushTestResultDto,
    operation_id="testMyPushSubscription",
)
async def test_my_push_subscription(
    id: str, session: Db, principal: Me, settings: Config
) -> PushTestResultDto:
    """Send one message to this device now and say what the push service answered."""
    sender = senders.get_sender(settings)
    if sender is None:
        raise _not_configured(status.HTTP_409_CONFLICT)
    row = (
        await session.execute(
            select(PushSubscription).where(
                PushSubscription.id == id, PushSubscription.user_id == principal.user_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Push subscription", id)
    target = Target(row.endpoint, row.p256dh, row.auth)
    await session.rollback()  # no transaction is held across the call to the push service
    payload = {
        "kind": "push.test",
        "title": "Test notification",
        "body": "This device is told by this instance.",
        "org": "",
        "ref": {"type": "none", "id": id},
        "url": "/account",
        "at": utcnow().isoformat(),
    }
    code = await attempt(sender, target, payload)
    delivered = code is not None and 200 <= code < 300
    gone = code in GONE
    await record_outcome(id, ok=delivered, gone=gone)
    return PushTestResultDto(delivered=delivered, status_code=code, removed=gone)
