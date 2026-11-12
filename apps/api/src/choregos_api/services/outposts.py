# SPDX-License-Identifier: Apache-2.0
"""Outposts: the platform's records of them and what moves those records (ADRs 0147, 0148).

Two callers. The management routes (`routers/outposts.py`) act for a person who holds
`outpost:manage`. The outposts' door (`outposts/`) acts for an instance: `admit_enrolment` and `enrol`
are what it calls before it knows who is speaking, and `scope_to_outpost` is how a request that names
an instance gets its scope.

Row-level security (ADR 0148 §6). `outposts` is the organisation's registry: its administrator lists
it under the organisation's scope alone, and a session that names an outpost sees that one row only.
The rows that belong to one outpost (`outpost_instances`, `outpost_enrolments`, `outpost_calls`) also
require the session to name it, and fail closed. So every function below that touches them names the
outpost first (`in_outpost_scope`), and flushes what it wrote before it stops naming it. The `WHERE`
clauses still say the outpost: SQLite has no row-level security, and a policy is a second lock, not
the first.

This module is imported by the door: it must import nothing that reaches an organisation's connectors
(ADR 0148 §12).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from choregos_contracts import (
    MAX_WAIT_S,
    OutpostAnswerOutcome,
    OutpostCallKind,
    OutpostCallState,
    OutpostCapability,
    OutpostDoorCode,
    OutpostEnrolmentRequest,
    OutpostInstanceStatus,
    OutpostRefusalCode,
    OutpostStatus,
    arguments_sha256,
    fingerprint_of,
)
from choregos_core import utcnow
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..config import get_settings
from ..db.base import uuid7
from ..db.models import (
    Organization,
    OrgConnector,
    Outpost,
    OutpostCall,
    OutpostEnrolment,
    OutpostInstance,
    Project,
)
from ..db.session import ALL_ORGS, in_outpost_scope, limit_to_organisations, name_the_outpost
from ..errors import ApiError, conflict, forbidden, not_found
from ..freshness import require_fresh_authentication
from ..rbac import Permission, Principal, outpost_principal
from ..schemas.outposts import (
    OutpostCallRecord,
    OutpostCreate,
    OutpostDto,
    OutpostEnrolmentCreate,
    OutpostEnrolmentCreated,
    OutpostInstanceCounts,
    OutpostInstanceDto,
)
from ..secret_store import (
    NO_KEY_MESSAGE,
    Keyring,
    KeyWrapper,
    Sealed,
    SecretStoreUnavailable,
    active_key_wrapper,
    open_sealed,
    seal,
)

#: An instance is online when it polled within this many seconds: a poll waits `MAX_WAIT_S` at most
#: and the daemon polls again at once, so two polls and a margin. "Offline" on an agent's access tab
#: is the same window (ADR 0149 §10).
POLL_WINDOW_S: Final = 2 * MAX_WAIT_S + 10
#: How old the authentication of the person who approves an instance may be, in minutes: what a
#: decision on a governed action asks by default (`services/actions.py`, `decide`).
APPROVAL_STEP_UP_MINUTES: Final = 10
#: What an enrolment token starts with. The 32 characters after it are its outpost's id: the door has
#: to name the outpost before row-level security lets it read the token's row.
ENROLMENT_TOKEN_PREFIX: Final = "choe_"  # noqa: S105 - a prefix, not a secret
#: How many of an outpost's calls its list reads back, newest first.
RECENT_CALLS: Final = 1000
#: The audit event the door raises when a second poll arrives under a key that already has one
#: waiting (ADR 0148 §7): what a stolen key looks like. `/metrics` counts them.
POLL_CONFLICT: Final = "outpost.poll_conflict"

_ACTIVE, _PAUSED, _REVOKED = (
    OutpostStatus.ACTIVE.value,
    OutpostStatus.PAUSED.value,
    OutpostStatus.REVOKED.value,
)
_PENDING = OutpostInstanceStatus.PENDING.value
_INSTANCE_ORDER = {
    _PENDING: 0,
    OutpostInstanceStatus.ACTIVE.value: 1,
    OutpostInstanceStatus.REVOKED.value: 2,
}
_HEX = frozenset("0123456789abcdef")


def _aware(moment: datetime) -> datetime:
    """SQLite returns naive datetimes; PostgreSQL returns UTC ones."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _when(moment: datetime | None) -> datetime | None:
    """A moment as an answer carries it: with its offset, whatever the database gave back."""
    return _aware(moment) if moment is not None else None


# ───────────────────────── refusals and rights ─────────────────────────


def refusal(code: str, detail: str, outpost: str | None = None, **extra: Any) -> ApiError:
    """A 409 of the contract's `OutpostConflict`: a problem with its `code`."""
    named = {"outpost": outpost} if outpost else {}
    return conflict(detail, extra={"code": code, **named, **extra})


def require_manage(principal: Principal, org: str) -> None:
    """Outposts are the organisation administrator's (ADR 0147 §4)."""
    if not principal.can(Permission.OUTPOST_MANAGE, org):
        raise forbidden(f"the outposts of {org} are managed by its administrator (`outpost:manage`)")


def require_person(principal: Principal) -> None:
    """An approval needs a person's session that carries a recent authentication, the condition
    `decide` puts on a decision: a token cannot approve (403), and a session that is too old is sent
    to authenticate again (401 `step_up_required`)."""
    if principal.kind != "user" or principal.authenticated_at is None:
        raise forbidden(
            "an instance is approved in a person's session, not with an API token (approval_requires_session)"
        )
    require_fresh_authentication(principal, APPROVAL_STEP_UP_MINUTES)


def _disabled() -> ApiError:
    return refusal(
        "outposts_disabled",
        "outposts are switched off on this deployment (`CHOREGOS_OUTPOSTS`): its operator turns them on",
    )


# ───────────────────────── ids that carry their outpost ─────────────────────────


def _compact(outpost_id: str) -> str:
    return outpost_id.replace("-", "")


def _outpost_id_in(text: str) -> str | None:
    """The outpost id written as the first 32 hexadecimal characters of `text`, as the table keeps it."""
    head = text[:32]
    if len(head) != 32 or not set(head) <= _HEX:
        return None
    return f"{head[:8]}-{head[8:12]}-{head[12:16]}-{head[16:20]}-{head[20:]}"


def outpost_id_of_instance(instance_id: str) -> str | None:
    """The outpost an instance id belongs to, read from the id itself; `None` when it carries none.

    It is a claim until the row is read: naming the outpost it gives only decides which rows
    row-level security may show, and the instance is then looked up among them."""
    return _outpost_id_in(instance_id)


def outpost_id_of_token(token: str) -> str | None:
    """The outpost an enrolment token says it is bound to; the digest then decides."""
    if not token.startswith(ENROLMENT_TOKEN_PREFIX):
        return None
    return _outpost_id_in(token[len(ENROLMENT_TOKEN_PREFIX) :])


def _new_instance_id(outpost: Outpost) -> str:
    return _compact(outpost.id) + _compact(uuid7())


def _new_token(outpost: Outpost) -> tuple[str, str]:
    """(the token, shown once; its digest, the only thing kept)."""
    token = ENROLMENT_TOKEN_PREFIX + _compact(outpost.id) + secrets.token_urlsafe(32)
    return token, token_digest(token)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def scope_to_outpost(session: AsyncSession, outpost_id: str) -> tuple[Outpost, str] | None:
    """Give the session the scope of one outpost of one organisation, from the outpost's id alone
    (ADR 0148 §6): `app.current_outpost` is that outpost and `app.current_orgs` its organisation.
    Returns the outpost and its organisation's current slug, or `None` when there is no such outpost:
    the session then sees no organisation at all.

    This is for a caller that has no principal yet: the door, with a token or a signature in hand.
    The organisation of an outpost is a row of `organizations`, which row-level security shows by
    slug: it cannot be read from a scope that does not hold it. So the outpost is named FIRST, then
    one statement reads that outpost and its organisation's slug under the all-organisations scope,
    as identity is resolved everywhere else (`deps.current_principal`), and the scope is narrowed
    before anything else runs. While that statement runs, the outposts' tables still show the named
    outpost only."""
    await name_the_outpost(session, outpost_id)
    found: tuple[Outpost, str] | None = None
    await limit_to_organisations(session, ALL_ORGS)
    try:
        row = (
            await session.execute(
                select(Outpost, Organization.slug)
                .join(Organization, Organization.id == Outpost.org_id)
                .where(Outpost.id == outpost_id)
            )
        ).first()
        if row is not None:
            found = (row[0], str(row[1]))
    finally:
        await limit_to_organisations(session, [found[1]] if found else None)
    return found


# ───────────────────────── what an outpost looks like ─────────────────────────


async def outpost_by_slug(session: AsyncSession, organisation: Organization, slug: str) -> Outpost:
    outpost = (
        await session.execute(select(Outpost).where(Outpost.org_id == organisation.id, Outpost.slug == slug))
    ).scalar_one_or_none()
    if outpost is None:
        raise not_found("Outpost", slug)
    return outpost


def _is_online(instance: OutpostInstance, now: datetime) -> bool:
    return (
        instance.status == OutpostInstanceStatus.ACTIVE.value
        and instance.last_seen_at is not None
        and now - _aware(instance.last_seen_at) <= timedelta(seconds=POLL_WINDOW_S)
    )


async def _instances(session: AsyncSession, outpost: Outpost) -> list[OutpostInstance]:
    async with in_outpost_scope(session, outpost.id):
        rows = (
            await session.execute(select(OutpostInstance).where(OutpostInstance.outpost_id == outpost.id))
        ).scalars()
        return sorted(rows, key=lambda i: (_INSTANCE_ORDER.get(i.status, 9), i.id))


async def connectors_of(session: AsyncSession, outpost: Outpost) -> list[str]:
    """The connectors that route to this outpost, by name (`org_connectors.outpost_id`)."""
    return sorted(
        (
            await session.execute(select(OrgConnector.name).where(OrgConnector.outpost_id == outpost.id))
        ).scalars()
    )


async def view(session: AsyncSession, outpost: Outpost) -> OutpostDto:
    """The outpost with what its instances say of it. Nothing of its side is here: none is stored."""
    instances = await _instances(session, outpost)
    now = utcnow()
    counted = {status.value: 0 for status in OutpostInstanceStatus}
    for instance in instances:
        counted[instance.status] += 1
    active = [i for i in instances if i.status == OutpostInstanceStatus.ACTIVE.value]
    seen = [_aware(i.last_seen_at) for i in instances if i.last_seen_at is not None]
    return OutpostDto(
        slug=outpost.slug,
        display_name=outpost.display_name,
        status=OutpostStatus(outpost.status),
        capabilities=[OutpostCapability(name) for name in outpost.capabilities or []],
        epoch=outpost.epoch,
        online=any(_is_online(i, now) for i in instances),
        last_seen_at=max(seen) if seen else None,
        instances=OutpostInstanceCounts(**counted),
        endpoints=sorted({alias for i in active for alias in (i.endpoints or [])}),
        connectors=await connectors_of(session, outpost),
        door_keys=get_settings().outposts_door_keys,
        created_by=outpost.created_by,
        created_at=_aware(outpost.created_at),
    )


async def list_outposts(session: AsyncSession, organisation: Organization) -> list[OutpostDto]:
    outposts = (
        await session.execute(select(Outpost).where(Outpost.org_id == organisation.id).order_by(Outpost.slug))
    ).scalars()
    return [await view(session, outpost) for outpost in list(outposts)]


def _instance_view(instance: OutpostInstance, in_flight: int, now: datetime) -> OutpostInstanceDto:
    return OutpostInstanceDto(
        id=instance.id,
        status=OutpostInstanceStatus(instance.status),
        fingerprint=instance.fingerprint,
        daemon_version=instance.daemon_version,
        protocol=instance.protocol,
        capabilities=[OutpostCapability(name) for name in instance.capabilities or []],
        endpoints=list(instance.endpoints or []),
        facts=dict(instance.facts or {}),
        enrolled_at=_aware(instance.enrolled_at),
        approved_by=instance.approved_by,
        approved_at=_when(instance.approved_at),
        revoked_by=instance.revoked_by,
        revoked_at=_when(instance.revoked_at),
        last_seen_at=_when(instance.last_seen_at),
        online=_is_online(instance, now),
        in_flight=in_flight,
    )


async def _in_flight(session: AsyncSession, outpost: Outpost) -> dict[str, int]:
    """The calls each instance holds and has not answered. The caller names the outpost."""
    rows = await session.execute(
        select(OutpostCall.instance_id, func.count())
        .where(
            OutpostCall.outpost_id == outpost.id,
            OutpostCall.state == OutpostCallState.CLAIMED.value,
            OutpostCall.instance_id.is_not(None),
        )
        .group_by(OutpostCall.instance_id)
    )
    return {str(instance_id): int(count) for instance_id, count in rows}


async def list_instances(session: AsyncSession, outpost: Outpost) -> list[OutpostInstanceDto]:
    """Pending ones first: they are what waits for a person."""
    instances = await _instances(session, outpost)
    async with in_outpost_scope(session, outpost.id):
        held = await _in_flight(session, outpost)
    now = utcnow()
    return [_instance_view(i, held.get(i.id, 0), now) for i in instances]


async def _instance(session: AsyncSession, outpost: Outpost, instance_id: str) -> OutpostInstance:
    """One instance of this outpost, or the 404. The caller names the outpost."""
    instance = (
        await session.execute(
            select(OutpostInstance).where(
                OutpostInstance.outpost_id == outpost.id, OutpostInstance.id == instance_id
            )
        )
    ).scalar_one_or_none()
    if instance is None:
        raise not_found("Outpost instance", instance_id)
    return instance


async def _one_instance_view(
    session: AsyncSession, outpost: Outpost, instance: OutpostInstance
) -> OutpostInstanceDto:
    held = await _in_flight(session, outpost)
    return _instance_view(instance, held.get(instance.id, 0), utcnow())


# ───────────────────────── what a person does to an outpost ─────────────────────────


async def _locked(session: AsyncSession, outpost: Outpost) -> None:
    """Serialise the moves of one outpost (a row lock on PostgreSQL, nothing on SQLite), and read it
    again: the state a move is judged on is the one the lock protects."""
    await session.execute(select(Outpost.id).where(Outpost.id == outpost.id).with_for_update())
    await session.refresh(outpost)


async def _move_epoch(session: AsyncSession, outpost: Outpost) -> None:
    """A revocation moves the epoch (ADR 0147 §5): nothing in flight is taken under the former one.
    Incremented in the database, so that two revocations never write the same number."""
    await session.flush()
    await session.execute(
        update(Outpost)
        .where(Outpost.id == outpost.id)
        .values(epoch=Outpost.epoch + 1)
        .execution_options(synchronize_session=False)
    )
    await session.refresh(outpost)


async def create_outpost(
    session: AsyncSession, organisation: Organization, body: OutpostCreate, principal: Principal
) -> Outpost:
    if not get_settings().outposts:
        raise _disabled()
    exists = refusal(
        "outpost_exists", f"`{body.slug}` is already an outpost of {organisation.slug}", body.slug
    )
    taken = (
        await session.execute(
            select(Outpost.id).where(Outpost.org_id == organisation.id, Outpost.slug == body.slug)
        )
    ).first()
    if taken is not None:
        raise exists
    outpost = Outpost(
        org_id=organisation.id,
        slug=body.slug,
        display_name=body.display_name or body.slug,
        status=_ACTIVE,
        capabilities=list(body.capabilities),
        epoch=0,
        created_by=principal.email,
    )
    try:
        async with session.begin_nested():
            session.add(outpost)
            await session.flush()
    except IntegrityError:  # created by someone else between the look and the write
        raise exists from None
    return outpost


async def pause_outpost(session: AsyncSession, outpost: Outpost) -> bool:
    """Whether it changed anything: pausing a paused outpost does not."""
    await _locked(session, outpost)
    if outpost.status == _REVOKED:
        raise refusal("outpost_revoked", f"`{outpost.slug}` is revoked: that is final", outpost.slug)
    if outpost.status == _PAUSED:
        return False
    outpost.status = _PAUSED
    await session.flush()
    return True


async def resume_outpost(session: AsyncSession, outpost: Outpost) -> bool:
    """Whether it changed anything: resuming an outpost that is not paused does not."""
    await _locked(session, outpost)
    if outpost.status == _REVOKED:
        raise refusal("outpost_revoked", f"`{outpost.slug}` is revoked: that is final", outpost.slug)
    if outpost.status != _PAUSED:
        return False
    outpost.status = _ACTIVE
    await session.flush()
    return True


async def revoke_outpost(session: AsyncSession, outpost: Outpost, principal: Principal) -> bool:
    """Revoke the outpost and every instance of it, and move its epoch (ADR 0147 §5). Final. Whether
    it changed anything: revoking a revoked outpost does not, and does not move the epoch again."""
    await _locked(session, outpost)
    if outpost.status == _REVOKED:
        return False
    async with in_outpost_scope(session, outpost.id):
        await session.execute(
            update(OutpostInstance)
            .where(OutpostInstance.outpost_id == outpost.id, OutpostInstance.status != _REVOKED)
            .values(status=_REVOKED, revoked_by=principal.email, revoked_at=utcnow())
            .execution_options(synchronize_session=False)
        )
    outpost.status = _REVOKED
    await _move_epoch(session, outpost)
    return True


async def delete_outpost(session: AsyncSession, outpost: Outpost) -> None:
    """Remove a revoked outpost, with its instances and what is left of its calls. Refused while
    connectors still route to it, then while it is not revoked: revoking is the one gesture that
    moves the epoch, and a deletion must not stand in for it."""
    await _locked(session, outpost)
    serving = await connectors_of(session, outpost)
    if serving:
        raise refusal(
            "outpost_in_use",
            f"`{outpost.slug}` still has connectors that route to it: route them elsewhere or delete them",
            outpost.slug,
            connectors=serving,
        )
    if outpost.status != _REVOKED:
        raise refusal(
            "outpost_not_revoked", f"`{outpost.slug}` is {outpost.status}: revoke it first", outpost.slug
        )
    await session.delete(outpost)
    await session.flush()


async def create_enrolment(
    session: AsyncSession, outpost: Outpost, body: OutpostEnrolmentCreate, principal: Principal
) -> OutpostEnrolmentCreated:
    """An enrolment token for one instance: single use, 15 minutes at most, kept as a digest, shown
    here and never again (ADR 0147 §4). It comes with what the operator writes in the outpost's
    policy file before the daemon starts: the door's origin and the door's keys to pin."""
    settings = get_settings()
    if not settings.outposts:
        raise _disabled()
    door_keys = settings.outposts_door_keys
    if not door_keys:
        # An outpost makes only calls signed by a key its policy file pins (ADR 0148 §13): with no key
        # to pin, an instance enrolled now could never be handed anything.
        raise refusal(
            "door_key_missing",
            "the outposts' door has no signing key on this deployment "
            "(`CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY`): there is no key to pin, so no instance is enrolled",
        )
    if outpost.status == _REVOKED:
        raise refusal("outpost_revoked", f"`{outpost.slug}` is revoked: that is final", outpost.slug)
    token, digest = _new_token(outpost)
    enrolment = OutpostEnrolment(
        org_id=outpost.org_id,
        outpost_id=outpost.id,
        token_digest=digest,
        expires_at=utcnow() + timedelta(minutes=body.expires_in_minutes),
        created_by=principal.email,
    )
    async with in_outpost_scope(session, outpost.id):
        session.add(enrolment)
        await session.flush()
    return OutpostEnrolmentCreated(
        id=enrolment.id,
        token=token,
        expires_at=_aware(enrolment.expires_at),
        control_plane=settings.outposts_door_origin,
        door_keys=door_keys,
    )


async def approve_instance(
    session: AsyncSession, outpost: Outpost, instance_id: str, fingerprint: str, principal: Principal
) -> OutpostInstanceDto:
    """A person approves a pending instance against the fingerprint the daemon printed (ADR 0147 §4):
    only then is it `active`. The fingerprint sent must be the one the enrolment recorded."""
    require_person(principal)
    if outpost.status == _REVOKED:
        raise refusal("outpost_revoked", f"`{outpost.slug}` is revoked: that is final", outpost.slug)
    not_pending = refusal(
        "instance_not_pending", "this instance was already approved or revoked", outpost.slug
    )
    async with in_outpost_scope(session, outpost.id):
        instance = await _instance(session, outpost, instance_id)
        if instance.status != _PENDING:
            raise not_pending
        if not hmac.compare_digest(instance.fingerprint, fingerprint):
            raise refusal(
                "fingerprint_mismatch",
                "the fingerprint sent is not the one this instance enrolled with: compare it with what "
                "`choregos-outpost enrol` printed, and approve nothing that differs",
                outpost.slug,
            )
        # Compare-and-set: the row only moves while it is still pending.
        done = await session.execute(
            update(OutpostInstance)
            .where(
                OutpostInstance.outpost_id == outpost.id,
                OutpostInstance.id == instance.id,
                OutpostInstance.status == _PENDING,
            )
            .values(
                status=OutpostInstanceStatus.ACTIVE.value,
                approved_by=principal.email,
                approved_at=utcnow(),
            )
            .execution_options(synchronize_session=False)
        )
        if done.rowcount != 1:  # type: ignore[attr-defined]
            raise not_pending
        await session.refresh(instance)
        return await _one_instance_view(session, outpost, instance)


async def revoke_instance(
    session: AsyncSession, outpost: Outpost, instance_id: str, principal: Principal
) -> tuple[OutpostInstanceDto, bool]:
    """Revoke one instance, pending or active, and move the outpost's epoch: nothing in flight at the
    outpost is taken, from this instance or another (ADR 0147 §5). Final. The second value says
    whether it changed anything: revoking a revoked instance does not, and moves no epoch."""
    await _locked(session, outpost)
    async with in_outpost_scope(session, outpost.id):
        instance = await _instance(session, outpost, instance_id)
        changed = instance.status != _REVOKED
        if changed:
            instance.status = _REVOKED
            instance.revoked_by = principal.email
            instance.revoked_at = utcnow()
            await session.flush()
        shown = await _one_instance_view(session, outpost, instance)
    if changed:
        await _move_epoch(session, outpost)
    return shown, changed


async def list_calls(
    session: AsyncSession, outpost: Outpost, state: OutpostCallState | None
) -> list[OutpostCall]:
    """The recent calls routed to this outpost, newest first."""
    query = select(OutpostCall).where(OutpostCall.outpost_id == outpost.id)
    if state is not None:
        query = query.where(OutpostCall.state == state.value)
    async with in_outpost_scope(session, outpost.id):
        rows = await session.execute(
            query.order_by(OutpostCall.created_at.desc(), OutpostCall.id.desc()).limit(RECENT_CALLS)
        )
        return list(rows.scalars())


async def call_records(session: AsyncSession, calls: list[OutpostCall]) -> list[OutpostCallRecord]:
    """Digests and references only: no arguments, no result (ADR 0148 §11)."""
    connector_ids = {c.connector_id for c in calls if c.connector_id}
    project_ids = {c.project_id for c in calls if c.project_id}
    connectors: dict[str, str] = {}
    projects: dict[str, str] = {}
    if connector_ids:
        found = await session.execute(
            select(OrgConnector.id, OrgConnector.name).where(OrgConnector.id.in_(connector_ids))
        )
        connectors = {str(row[0]): str(row[1]) for row in found}
    if project_ids:
        found = await session.execute(select(Project.id, Project.slug).where(Project.id.in_(project_ids)))
        projects = {str(row[0]): str(row[1]) for row in found}
    return [
        OutpostCallRecord(
            id=call.id,
            kind=OutpostCallKind(call.kind),
            state=OutpostCallState(call.state),
            connector=connectors.get(call.connector_id or ""),
            endpoint=call.endpoint,
            tool=call.tool,
            write=call.write,
            instance_id=call.instance_id,
            epoch=call.epoch,
            outcome=OutpostAnswerOutcome(call.outcome) if call.outcome else None,
            is_error=call.is_error,
            refusal=OutpostRefusalCode(call.refusal) if call.refusal else None,
            arguments_sha256=call.arguments_sha256,
            result_sha256=call.result_sha256,
            project=projects.get(call.project_id or ""),
            run_id=call.run_id,
            action_id=call.action_id,
            created_at=_aware(call.created_at),
            claimed_at=_when(call.claimed_at),
            finished_at=_when(call.finished_at),
        )
        for call in calls
    ]


# ───────────────────────── what the door does: an instance enrols ─────────────────────────


class EnrolmentRefused(Exception):  # noqa: N818 - a decision of the door, not a failure
    """The door refuses an enrolment with one of its own codes (`outpost-door.yaml`):
    `invalid_enrolment_token` (unknown, used or expired: it does not say which) or
    `key_already_enrolled`. A refused enrolment does not use the token up."""

    def __init__(self, code: OutpostDoorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class Enrolled:
    """What an enrolment made: a pending instance, handed nothing until a person approves it."""

    instance: OutpostInstance
    outpost: Outpost
    #: The organisation's current slug.
    organisation: str
    #: The signing key was already a pending instance of this outpost: it is that instance again.
    again: bool


@dataclass(frozen=True, slots=True)
class EnrolmentAdmission:
    """An enrolment the door may go on with: the token is one of this outpost's, unused and in time,
    and the signing key is no instance of it, or one that is still pending."""

    outpost: Outpost
    #: The organisation's current slug.
    organisation: str
    enrolment: OutpostEnrolment
    #: The pending instance this signing key already is at this outpost, if any.
    known: OutpostInstance | None


async def admit_enrolment(session: AsyncSession, token: str, signing_key: str) -> EnrolmentAdmission:
    """Whether a token and a signing key may enrol, without using the token up.

    The door's contract answers a request for the first check that fails, and puts the token where
    the instance would be: before the rate, the protocol and the body's schema. So the door asks
    this first, with what it read of a body it has not judged yet, and `enrol` asks it again when it
    writes. The session has no scope yet: this gives it the token's own (`scope_to_outpost`).

    `EnrolmentRefused` as `enrol` raises it. A key enrolled at another outpost is not seen here: under
    row-level security the session reads this outpost's instances only, and the unique index refuses
    that key when the row is written."""
    invalid = EnrolmentRefused(OutpostDoorCode.INVALID_ENROLMENT_TOKEN)
    outpost_id = outpost_id_of_token(token)
    scope = await scope_to_outpost(session, outpost_id) if outpost_id else None
    if scope is None:
        raise invalid
    outpost, organisation = scope
    if outpost.status == _REVOKED:
        raise invalid
    enrolment = (
        await session.execute(
            select(OutpostEnrolment).where(
                OutpostEnrolment.outpost_id == outpost.id,
                OutpostEnrolment.token_digest == token_digest(token),
            )
        )
    ).scalar_one_or_none()
    if enrolment is None or enrolment.used_at is not None or _aware(enrolment.expires_at) <= utcnow():
        raise invalid
    known = (
        await session.execute(select(OutpostInstance).where(OutpostInstance.signing_key == signing_key))
    ).scalar_one_or_none()
    if known is not None and (known.outpost_id != outpost.id or known.status != _PENDING):
        raise EnrolmentRefused(OutpostDoorCode.KEY_ALREADY_ENROLLED)
    return EnrolmentAdmission(outpost=outpost, organisation=organisation, enrolment=enrolment, known=known)


async def enrol(session: AsyncSession, request: OutpostEnrolmentRequest) -> Enrolled:
    """Consume an enrolment token: the instance is born `pending` (ADR 0147 §4).

    The caller is the door, which has verified that the request is signed with the key it enrols; it
    has no scope yet, and this function gives the session the token's own (`scope_to_outpost`).

    A token enrols one instance, once. A signing key that is still pending at the token's own outpost
    enrols again as the same instance under the new token: a daemon whose first answer was lost keeps
    its keys. A key that was ever approved or revoked, or that is pending at another outpost, is
    refused: a revoked key does not come back. The token is used up in the same savepoint as the
    instance is written, so a refused enrolment leaves it as it was."""
    invalid = EnrolmentRefused(OutpostDoorCode.INVALID_ENROLMENT_TOKEN)
    taken = EnrolmentRefused(OutpostDoorCode.KEY_ALREADY_ENROLLED)
    admitted = await admit_enrolment(session, request.token, request.signing_key)
    outpost, organisation = admitted.outpost, admitted.organisation
    enrolment, known = admitted.enrolment, admitted.known
    now = utcnow()
    instance = known or OutpostInstance(
        id=_new_instance_id(outpost),
        org_id=outpost.org_id,
        outpost_id=outpost.id,
        status=_PENDING,
        signing_key=request.signing_key,
        fingerprint=fingerprint_of(request.signing_key),
    )
    try:
        async with session.begin_nested():
            used = await session.execute(
                update(OutpostEnrolment)
                .where(
                    OutpostEnrolment.outpost_id == outpost.id,
                    OutpostEnrolment.id == enrolment.id,
                    OutpostEnrolment.used_at.is_(None),
                )
                .values(used_at=now, instance_id=instance.id)
                .execution_options(synchronize_session=False)
            )
            if used.rowcount != 1:  # type: ignore[attr-defined]
                raise invalid  # used by another request since it was read
            instance.sealing_key = request.sealing_key
            instance.facts = dict(request.facts)
            instance.daemon_version = request.daemon_version
            instance.protocol = request.protocol
            instance.capabilities = list(request.capabilities)
            instance.enrolled_at = now
            session.add(instance)
            await session.flush()
    except IntegrityError:
        raise taken from None
    await record(
        session,
        outpost_principal(outpost_id=outpost.id, instance_id=instance.id),
        "outpost.enrol",
        org_id=outpost.org_id,
        target_type="outpost_instance",
        target_id=instance.id,
        outpost=outpost.slug,
        enrolment=enrolment.id,
        fingerprint=instance.fingerprint,
        again=known is not None,
    )
    return Enrolled(instance=instance, outpost=outpost, organisation=organisation, again=known is not None)


# ───────────────────────── a call's arguments, while they wait ─────────────────────────


def _arguments_name(call: OutpostCall) -> str:
    """What the envelope is bound to, with the organisation: this outpost, this call."""
    return f"outpost/{call.outpost_id}/call/{call.id}"


def seal_call_arguments(
    call: OutpostCall, arguments: dict[str, Any], wrapper: KeyWrapper | Keyring | None = None
) -> None:
    """Put a call's arguments on its row, sealed under the deployment's key wrapper (ADR 0103), with
    their digest in clear (ADR 0148 §11). The envelope is the one of `org_secrets`: a data key per
    call, wrapped under the key the deployment holds, bound to the organisation, the outpost and the
    call's id, so that it opens on no other row. Without a key nothing is written."""
    wrapper = wrapper or active_key_wrapper()
    if wrapper is None:
        raise SecretStoreUnavailable(NO_KEY_MESSAGE)
    plain = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    sealed = seal(plain, wrapper, call.org_id, _arguments_name(call), 1)
    call.arguments_ciphertext, call.arguments_nonce = sealed.ciphertext, sealed.nonce
    call.arguments_wrapped_dek, call.arguments_dek_nonce = sealed.wrapped_dek, sealed.dek_nonce
    call.arguments_kek_id = sealed.kek_id
    call.arguments_sha256 = arguments_sha256(arguments)


def open_call_arguments(
    call: OutpostCall, wrapper: KeyWrapper | Keyring | None = None
) -> dict[str, Any] | None:
    """The arguments that wait on a call's row; `None` when it holds none (a tool listing, or a call
    that completed). `SecretStoreUnavailable` when the envelope does not open here."""
    if (
        call.arguments_ciphertext is None
        or call.arguments_nonce is None
        or call.arguments_wrapped_dek is None
        or call.arguments_dek_nonce is None
        or call.arguments_kek_id is None
    ):
        return None
    wrapper = wrapper or active_key_wrapper()
    if wrapper is None:
        raise SecretStoreUnavailable(NO_KEY_MESSAGE)
    sealed = Sealed(
        ciphertext=call.arguments_ciphertext,
        nonce=call.arguments_nonce,
        wrapped_dek=call.arguments_wrapped_dek,
        dek_nonce=call.arguments_dek_nonce,
        kek_id=call.arguments_kek_id,
    )
    opened: dict[str, Any] = json.loads(open_sealed(sealed, wrapper, call.org_id, _arguments_name(call), 1))
    return opened


def clear_call_arguments(call: OutpostCall) -> None:
    """Empty the arguments once the call completes; `arguments_sha256` stays (ADR 0148 §11), and so
    does everything else the row says: its claim, and what the door recorded for it."""
    call.arguments_ciphertext = call.arguments_nonce = None
    call.arguments_wrapped_dek = call.arguments_dek_nonce = None
    call.arguments_kek_id = None


__all__ = [
    "APPROVAL_STEP_UP_MINUTES",
    "ENROLMENT_TOKEN_PREFIX",
    "POLL_CONFLICT",
    "POLL_WINDOW_S",
    "Enrolled",
    "EnrolmentAdmission",
    "EnrolmentRefused",
    "admit_enrolment",
    "approve_instance",
    "call_records",
    "clear_call_arguments",
    "connectors_of",
    "create_enrolment",
    "create_outpost",
    "delete_outpost",
    "enrol",
    "list_calls",
    "list_instances",
    "list_outposts",
    "open_call_arguments",
    "outpost_by_slug",
    "outpost_id_of_instance",
    "outpost_id_of_token",
    "pause_outpost",
    "refusal",
    "require_manage",
    "require_person",
    "resume_outpost",
    "revoke_instance",
    "revoke_outpost",
    "scope_to_outpost",
    "seal_call_arguments",
    "token_digest",
    "view",
]
