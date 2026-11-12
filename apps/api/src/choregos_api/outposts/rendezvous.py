# SPDX-License-Identifier: Apache-2.0
"""The rendezvous: a call through an outpost is a row with a short life (ADR 0148 §7 and §11, ADR 0149 §4-5).

Two sides meet on a row of `outpost_calls` and nowhere else.

- **Whoever routes a call to an outpost** (the broker's client, S65-13) places it (`place`), waits for
  what the door records (`wait`), may stop waiting (`cancel`) and empties what is left once it has
  read it (`forget`). A write's id is derived from its effect's key, so a retried effect meets the
  first row (`Placed.created` is false) and creates no second delivery.
- **The door** claims calls for the instance that polls (`claim`), seals each to that instance's key
  and signs it with the door's (ADR 0148 §13), records the answer or the refusal once (`record`), and
  tells an instance which of the calls it holds are awaited no more (`no_longer_awaited`).

Delivery (ADR 0149 §5). A call is claimed once: the claim is a compare-and-set on a row no instance
ever claimed, so of two claimers one wins, a claimed write is never handed over again, and a read
whose lease ended is not handed over again under its id either: the journal of the instance that
held it would refuse the copy. A read that timed out is asked again as a new call. Only a call that
no instance claimed (`expired`) may be placed again under the same id.

Row-level security (ADR 0148 §6). Every statement here runs in a session that names the call's
outpost inside its organisation's scope: the door's is the one its caller's signature resolved to,
and the functions a caller of the platform's side uses name the outpost themselves. `sweep` is the
platform's: under its scope no outpost's row exists until an outpost is named, so it lists the
registry and names each in turn.

The wake-up (ADR 0148 §7). A second channel on the connection every replica already listens on
(`events.PostgresRelay.listen_also`), which carries ids and nothing else: an outpost's id says "look
at this outpost's calls" to the polls that wait for it, and an outpost's id followed by a call's says
"this call moved" to whoever waits for that call. The notification is sent in the transaction of the
row it announces, so it leaves with that commit and never before (ADR 0085). On SQLite there is no
relay: whoever waits looks again periodically, which is also what covers a notification lost across
a reconnection.

This module is part of the door's package: it imports nothing that reaches an organisation's
connectors (ADR 0148 §12).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import random
import re
import weakref
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Final

import jwt
from choregos_adapters import sealing
from choregos_contracts import (
    MAX_CONCURRENCY,
    MAX_LEASE_S,
    SIGNATURE_ALGORITHM,
    OutpostAnswer,
    OutpostAnswerOutcome,
    OutpostAssertedNames,
    OutpostCallKind,
    OutpostCallState,
    OutpostCapability,
    OutpostInstanceStatus,
    OutpostRefusalCode,
    OutpostStatus,
    arguments_sha256,
    call_signature_claims,
    call_signature_header,
)

# `MAX_SEALED_CALL_BYTES`: the most one element of a poll's `calls` weighs as carried (its id, its
# envelope and its signature together); a call that would weigh more is not handed over.
from choregos_contracts.outpost import ENDPOINT_ALIAS_PATTERN, MAX_SEALED_CALL_BYTES, OUTPOST_ID_PATTERN
from choregos_core import utcnow
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from pydantic import ValidationError
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ed25519_public_key, get_settings
from ..db.base import uuid7
from ..db.models import Outpost, OutpostCall, OutpostInstance
from ..db.session import ALL_ORGS, get_sessionmaker, in_outpost_scope, run_after_commit, session_scope
from ..logging import get_logger
from ..secret_store import (
    NO_KEY_MESSAGE,
    KeyWrapper,
    Sealed,
    SecretStoreUnavailable,
    active_key_wrapper,
    open_sealed,
    seal,
)
from ..services import outposts as service
from ..services.budgets import lock_budget_scopes

logger = get_logger("choregos.outposts.rendezvous")

#: The second channel of the relay's connection (ADR 0148 §7). Its payload is an id, or two.
CHANNEL: Final = "choregos_outpost_calls"
#: Whoever waits looks again after this long, at random between the two, whatever it heard: a
#: notification can be lost across a reconnection, and on SQLite there is none (ADR 0148 §7).
LOOK_S: tuple[float, float] = (5.0, 10.0)
#: How often the platform closes what ran out of time at outposts nobody polls or calls any more.
SWEEP_S: float = 30.0
#: How long an answer nobody read stays on its row before the sweep empties it.
ANSWER_KEPT_S: Final = 900

_PENDING, _CLAIMED = OutpostCallState.PENDING.value, OutpostCallState.CLAIMED.value
_ANSWERED, _REFUSED = OutpostCallState.ANSWERED.value, OutpostCallState.REFUSED.value
_EXPIRED, _TIMED_OUT = OutpostCallState.EXPIRED.value, OutpostCallState.TIMED_OUT.value
_IN_DOUBT = OutpostCallState.IN_DOUBT.value
#: A row still waits for something: a claim, or an answer.
_WAITING = (_PENDING, _CLAIMED)
_TOOLS = OutpostCapability.TOOLS.value

#: What a row holds while it waits and loses when its call completes (ADR 0148 §11): the arguments,
#: the asserted names and the private half of the answer key. The digests stay.
_EMPTIED: dict[str, None] = {
    "arguments_ciphertext": None,
    "arguments_nonce": None,
    "arguments_wrapped_dek": None,
    "arguments_dek_nonce": None,
    "arguments_kek_id": None,
    "asserted": None,
}
_NO_ANSWER_KEY: dict[str, None] = {
    "answer_key_ciphertext": None,
    "answer_key_nonce": None,
    "answer_key_wrapped_dek": None,
    "answer_key_dek_nonce": None,
    "answer_key_kek_id": None,
}


def _aware(moment: datetime) -> datetime:
    """SQLite returns naive datetimes; PostgreSQL returns UTC ones."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text_: str) -> bytes:
    return base64.urlsafe_b64decode(text_ + "=" * (-len(text_) % 4))


# ───────────────────────── who waits in this process, and what wakes them ─────────────────────────


class Waiters:
    """Who waits in this process, by what they wait for. Nothing here crosses a process: the relay
    hands every replica the notification, and each wakes its own."""

    def __init__(self) -> None:
        self._waiting: dict[str, set[asyncio.Event]] = {}
        #: Notifications the relay handed over, whether or not anybody here waited for them.
        self.heard = 0

    @contextlib.contextmanager
    def on(self, key: str) -> Iterator[asyncio.Event]:
        """An event set whenever `key` is announced, for the length of the block."""
        event = asyncio.Event()
        self._waiting.setdefault(key, set()).add(event)
        try:
            yield event
        finally:
            waiting = self._waiting.get(key)
            if waiting is not None:
                waiting.discard(event)
                if not waiting:
                    del self._waiting[key]

    def wake(self, key: str) -> None:
        for event in self._waiting.get(key, ()):
            event.set()

    def waiting(self, key: str | None = None) -> int:
        """How many wait for `key`, or for anything."""
        if key is not None:
            return len(self._waiting.get(key, ()))
        return sum(len(waiting) for waiting in self._waiting.values())


_waiters = Waiters()


def waiters() -> Waiters:
    return _waiters


def woken(payload: str) -> None:
    """What the relay calls with a notification of `CHANNEL`: the payload is the key."""
    _waiters.heard += 1
    _waiters.wake(payload)


def calls_key(outpost_id: str) -> str:
    """What the polls of an outpost wait for: something to look at among its calls."""
    return outpost_id


def call_key(outpost_id: str, call_id: str) -> str:
    """What whoever placed a call waits for: that call moved."""
    return f"{outpost_id}/{call_id}"


async def announce(session: AsyncSession, key: str) -> None:
    """Wake whoever waits for `key`, in every replica, once this transaction has committed: the
    notification is part of it and leaves with it, never before (ADR 0085). Ids only. On SQLite there
    is no relay, and the periodic look serves."""
    if get_settings().is_sqlite:
        return
    await session.execute(text("SELECT pg_notify(:channel, :key)"), {"channel": CHANNEL, "key": key})


# ───────────────────────── a short transaction under one outpost's scope ─────────────────────────


@dataclass(frozen=True, slots=True)
class Scope:
    session: AsyncSession
    outpost: Outpost
    #: The organisation's current slug.
    organisation: str


@contextlib.asynccontextmanager
async def in_scope(outpost_id: str) -> AsyncIterator[Scope | None]:
    """A transaction of its own under the scope of one outpost of one organisation, committed when
    the block ends; `None` when there is no such outpost. Whoever waits for something takes one per
    look and holds no connection in between (ADR 0148 §7).

    The scope is S65-11's (`scope_to_outpost`): the outpost is named first, its organisation read
    from it, and the session narrowed to that organisation before anything else runs."""
    async with get_sessionmaker()() as session:
        try:
            found = await service.scope_to_outpost(session, outpost_id)
            yield Scope(session, found[0], found[1]) if found is not None else None
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        await run_after_commit(session)


# ───────────────────────── the keys ─────────────────────────


@lru_cache(maxsize=4)
def _door_key(private_pem: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    if not isinstance(key, Ed25519PrivateKey):  # the settings already refused it at start-up
        raise ValueError("the door's signing key is not an Ed25519 key")
    return key


def sign_call(private_pem: str, *, call_id: str, instance_id: str, sealed: str) -> str:
    """The door's signature of a call it hands over (ADR 0148 §13): a compact JWS whose `kid` is the
    signing key itself and whose claims are this call, for this instance, in this envelope, and
    nothing else. An outpost makes only calls signed this way by a key its policy file pins."""
    return jwt.encode(
        call_signature_claims(call_id=call_id, instance_id=instance_id, sealed=sealed),
        _door_key(private_pem),
        algorithm=SIGNATURE_ALGORITHM,
        headers=call_signature_header(ed25519_public_key(private_pem)),
    )


def _answer_key_name(call: OutpostCall) -> str:
    """What the envelope of the answer key's private half is bound to: this outpost, this call."""
    return f"outpost/{call.outpost_id}/call/{call.id}/answer-key"


def _wrapper() -> KeyWrapper:
    wrapper = active_key_wrapper()
    if wrapper is None:
        raise SecretStoreUnavailable(NO_KEY_MESSAGE)
    return wrapper


def _new_answer_key(call: OutpostCall) -> None:
    """A key pair for this one call's answer (ADR 0148 §5). The public half goes inside the sealed
    call, where no proxy can rewrite it; the private half waits under the key wrapper, beside the
    arguments, for whoever placed the call. The door never opens it."""
    private = X25519PrivateKey.generate()
    raw = private.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    kept = seal(_b64(raw), _wrapper(), call.org_id, _answer_key_name(call), 1)
    call.answer_key = _b64(public)
    call.answer_key_ciphertext, call.answer_key_nonce = kept.ciphertext, kept.nonce
    call.answer_key_wrapped_dek, call.answer_key_dek_nonce = kept.wrapped_dek, kept.dek_nonce
    call.answer_key_kek_id = kept.kek_id


def _answer_private_key(call: OutpostCall) -> X25519PrivateKey | None:
    if (
        call.answer_key_ciphertext is None
        or call.answer_key_nonce is None
        or call.answer_key_wrapped_dek is None
        or call.answer_key_dek_nonce is None
        or call.answer_key_kek_id is None
    ):
        return None
    kept = Sealed(
        ciphertext=call.answer_key_ciphertext,
        nonce=call.answer_key_nonce,
        wrapped_dek=call.answer_key_wrapped_dek,
        dek_nonce=call.answer_key_dek_nonce,
        kek_id=call.answer_key_kek_id,
    )
    raw = _unb64(open_sealed(kept, _wrapper(), call.org_id, _answer_key_name(call), 1))
    return X25519PrivateKey.from_private_bytes(raw)


# ───────────────────────── a call as it crosses the door ─────────────────────────


def _moment(moment: datetime) -> str:
    return _aware(moment).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plaintext(call: OutpostCall, arguments: dict[str, Any] | None, deadline: datetime) -> dict[str, Any]:
    """What the claiming instance reads once it has opened the envelope: `outpost-call.schema.json`.
    It says nothing of whether the tool writes: the outpost reads that from its own server."""
    return {
        "id": call.id,
        "kind": call.kind,
        "endpoint": call.endpoint,
        "tool": call.tool,
        "arguments": arguments,
        "deadline": _moment(deadline),
        "answer_key": call.answer_key,
        "asserted": dict(call.asserted or {}),
    }


def carried_bytes(element: dict[str, str]) -> int:
    """What one element of a poll's `calls` weighs as the door writes it: compact JSON, UTF-8."""
    return len(json.dumps(element, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _would_weigh(call: OutpostCall, arguments: dict[str, Any] | None, deadline: datetime) -> int:
    """What the call will weigh once sealed and signed, before it has a key or a claimer: the
    envelope is the plaintext with 60 bytes around it, in base64url; the answer key, the signature
    and the element's own punctuation are under a kilobyte. The claim weighs the real thing."""
    plain = len(json.dumps(_plaintext(call, arguments, deadline)).encode("utf-8"))
    return len(call.id) + 4 * ((plain + 60 + 2) // 3) + 1024


# ───────────────────────── the platform's side: placing a call ─────────────────────────

#: Why `place` wrote nothing.
SWITCHED_OFF: Final = "switched_off"
PAUSED: Final = "paused"
REVOKED: Final = "revoked"
OUTPOST_QUOTA: Final = "outpost_quota"
ORGANISATION_QUOTA: Final = "organisation_quota"
TOO_LARGE: Final = "too_large"
ID_TAKEN: Final = "id_taken"


class NotAdmitted(Exception):  # noqa: N818 - a decision at admission, not a failure
    """A call the rendezvous does not take: nothing was written and nothing will reach an outpost.
    `reason` is one of the constants above; whoever routes the call turns it into its own refusal
    (ADR 0149 §7: a paused or revoked outpost, and outposts switched off, are `OutpostPaused`)."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Quota:
    """Calls in flight, bounded where a call is admitted (ADR 0148 §10, in the line of ADR 0027): a
    call counts from the moment it is placed until it is answered, refused or out of time."""

    per_outpost: int
    per_organisation: int

    @classmethod
    def of_the_deployment(cls) -> Quota:
        settings = get_settings()
        return cls(
            per_outpost=settings.outposts_calls_in_flight_per_outpost,
            per_organisation=settings.outposts_calls_in_flight_per_organisation,
        )


@dataclass(frozen=True, slots=True)
class Placed:
    id: str
    state: OutpostCallState
    #: False when a row already answered for this id: a retry met the first call (ADR 0149 §5), and
    #: `state` is what became of it. Nothing was placed a second time.
    created: bool


def new_call_id() -> str:
    """An id for a call that has no key of its own: a read."""
    return uuid7()


def call_id_of(*parts: str) -> str:
    """A deterministic id for a call that must be placed once whatever is retried: a write's is
    derived from its effect's key (ADR 0149 §5). The same parts give the same id, which the table
    holds once."""
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _lock_scope(org_id: str) -> str:
    return f"outpost_calls:{org_id}"


async def _in_flight(session: AsyncSession, org_id: str) -> dict[str, int]:
    """The calls each outpost of an organisation has in flight. A session that names an outpost sees
    that outpost's calls only, so the registry is read with none named and each outpost is named in
    turn; what the session named before is restored."""
    held: dict[str, int] = {}
    async with in_outpost_scope(session, None):
        outposts = list((await session.execute(select(Outpost.id).where(Outpost.org_id == org_id))).scalars())
        for outpost_id in outposts:
            async with in_outpost_scope(session, outpost_id):
                held[outpost_id] = int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(OutpostCall)
                            .where(OutpostCall.outpost_id == outpost_id, OutpostCall.state.in_(_WAITING))
                        )
                    ).scalar_one()
                )
    return held


#: What a session holds while it places: the transaction that took the organisation's lock.
_PLACING: Final = "choregos.outposts.placing"


async def _one_placement_at_a_time(session: AsyncSession, org_id: str) -> None:
    """Serialise the placements of one organisation until this transaction ends: two calls placed at
    once would each count the same calls in flight, and both pass under the last place.

    The lock is the budgets' (`lock_budget_scopes`): an advisory lock on PostgreSQL, which a
    transaction may take twice, and on SQLite an in-process lock, which a task may not. So a
    transaction that places a second call remembers that it holds it."""
    scope = _lock_scope(org_id)
    if not get_settings().is_sqlite:
        await lock_budget_scopes(session, [scope])
        return
    if session.sync_session.get_transaction() is None:
        await session.execute(select(1))  # the transaction the lock will live as long as
    root = session.sync_session.get_transaction()
    held: tuple[weakref.ref[Any], set[str]] | None = session.info.get(_PLACING)
    if held is not None and held[0]() is root:
        if scope in held[1]:
            return
        scopes = held[1]
    else:
        scopes = set()
    await lock_budget_scopes(session, [scope])
    scopes.add(scope)
    session.info[_PLACING] = (weakref.ref(root), scopes)


def _check_the_call(
    call_id: str, kind: OutpostCallKind, endpoint: str, tool: str | None, arguments: Any, write: bool
) -> None:
    """A call is shaped as its kind says before anything is written. `ValueError`: the caller's fault."""
    listing = kind is OutpostCallKind.LIST_TOOLS
    if not re.fullmatch(OUTPOST_ID_PATTERN, call_id):
        raise ValueError("a call's id is one path segment of the door: letters, digits, `_` and `-`")
    if not re.fullmatch(ENDPOINT_ALIAS_PATTERN, endpoint):
        raise ValueError(f"`{endpoint}` is not an endpoint alias")
    if listing and (tool is not None or arguments is not None or write):
        raise ValueError("a tool listing names no tool, carries no arguments and writes nothing")
    if not listing and (not tool or not isinstance(arguments, dict)):
        raise ValueError("a tool call names its tool and carries its arguments")


async def place(  # noqa: PLR0913 - one call is these facts, keyword-only
    session: AsyncSession,
    outpost: Outpost,
    *,
    call_id: str,
    kind: OutpostCallKind,
    endpoint: str,
    timeout_s: float,
    write: bool = False,
    tool: str | None = None,
    arguments: dict[str, Any] | None = None,
    asserted: dict[str, Any] | None = None,
    connector_id: str | None = None,
    project_id: str | None = None,
    run_id: str | None = None,
    action_id: str | None = None,
    quota: Quota | None = None,
    now: datetime | None = None,
) -> Placed:
    """Write a call for an outpost to claim, in the caller's transaction: it exists, and the polls
    are woken, when that transaction commits.

    `session` is the caller's, under the organisation's scope; the outpost is named here for what is
    written. `call_id` is the row's key: the same id is the same call, and a second `place` under it
    returns what became of the first (`created` false) unless no instance ever claimed it, in which
    case it is placed again. `timeout_s` is how long the platform waits, 300 seconds at most: a call
    nobody claimed by then is `expired`, and a lease never outlives it.

    The arguments wait sealed under the deployment's key wrapper (`SecretStoreUnavailable` without
    one), and so does the private half of a key made for this call's answer. `NotAdmitted` when the
    call is not taken: outposts switched off, the outpost paused or revoked, a quota of calls in
    flight reached, a call too large to cross the door, an id that is another call's."""
    settings = get_settings()
    if not settings.outposts:
        raise NotAdmitted(SWITCHED_OFF, "outposts are switched off on this deployment (`CHOREGOS_OUTPOSTS`)")
    if outpost.status != OutpostStatus.ACTIVE.value:
        reason = REVOKED if outpost.status == OutpostStatus.REVOKED.value else PAUSED
        raise NotAdmitted(reason, f"the outpost `{outpost.slug}` is {outpost.status}: it is handed nothing")
    kind = OutpostCallKind(kind)
    _check_the_call(call_id, kind, endpoint, tool, arguments, write)
    if not 0 < timeout_s <= MAX_LEASE_S:
        raise ValueError(f"a call waits {MAX_LEASE_S} seconds at most")
    names = OutpostAssertedNames.model_validate(asserted or {}).model_dump(exclude_defaults=True)
    digest = arguments_sha256(arguments) if arguments is not None else None
    now = now or utcnow()
    quota = quota or Quota.of_the_deployment()

    await _one_placement_at_a_time(session, outpost.org_id)
    async with in_outpost_scope(session, outpost.id):
        await _close(session, outpost.id, now)
        call = (
            await session.execute(
                select(OutpostCall)
                .where(OutpostCall.outpost_id == outpost.id, OutpostCall.id == call_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if call is not None:
            if (call.kind, call.endpoint, call.tool, bool(call.write), call.arguments_sha256) != (
                kind.value,
                endpoint,
                tool,
                write,
                digest,
            ):
                raise NotAdmitted(ID_TAKEN, f"the call `{call_id}` exists and is another call")
            # Only a call no instance ever claimed is placed again under its id: a claimed write is
            # never handed over twice, and the journal of the instance that held a read refuses a copy.
            if not (call.state == _EXPIRED and call.claimed_at is None and call.recorded is None):
                return Placed(id=call.id, state=OutpostCallState(call.state), created=False)

    held = await _in_flight(session, outpost.org_id)
    if held.get(outpost.id, 0) >= quota.per_outpost:
        raise NotAdmitted(
            OUTPOST_QUOTA,
            f"the outpost `{outpost.slug}` already has {quota.per_outpost} calls in flight",
        )
    if sum(held.values()) >= quota.per_organisation:
        raise NotAdmitted(
            ORGANISATION_QUOTA,
            f"the organisation's outposts already have {quota.per_organisation} calls in flight",
        )

    again = call is not None
    expires_at = now + timedelta(seconds=timeout_s)
    probe = OutpostCall(id=call_id, kind=kind.value, endpoint=endpoint, tool=tool, asserted=names)
    if _would_weigh(probe, arguments, expires_at) > MAX_SEALED_CALL_BYTES:
        raise NotAdmitted(TOO_LARGE, f"the call weighs more than {MAX_SEALED_CALL_BYTES} bytes once sealed")
    if call is None:
        call = OutpostCall(
            id=call_id,
            org_id=outpost.org_id,
            outpost_id=outpost.id,
            connector_id=connector_id,
            kind=kind.value,
            endpoint=endpoint,
            tool=tool,
            write=write,
            project_id=project_id,
            run_id=run_id,
            action_id=action_id,
        )
    call.state = _PENDING
    call.expires_at = expires_at
    call.finished_at = None
    call.asserted = names or None
    if arguments is not None:
        service.seal_call_arguments(call, arguments)
    _new_answer_key(call)
    async with in_outpost_scope(session, outpost.id):
        try:
            async with session.begin_nested():
                session.add(call)
                await session.flush()
        except IntegrityError:
            # The row this session cannot see: the id is a call of another outpost.
            raise NotAdmitted(ID_TAKEN, f"the call `{call_id}` exists and is another call") from None
        await announce(session, calls_key(outpost.id))
    return Placed(id=call.id, state=OutpostCallState.PENDING, created=not again)


# ───────────────────────── the door's side: claiming, recording ─────────────────────────


async def claim(
    session: AsyncSession,
    outpost: Outpost,
    instance: OutpostInstance,
    *,
    capacity: int,
    endpoints: list[str],
    capabilities: list[str],
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """Claim calls for the instance that polls and return them as they cross the door: each sealed to
    that instance's key and signed by the door (ADR 0148 §5, §13).

    At most `capacity`, only for the aliases the instance announced, only when the outpost is active,
    the instance approved and `tools` both announced and enabled. Of two claimers one wins: the claim
    is a compare-and-set on a row no instance ever claimed, and on PostgreSQL a row another claimer
    is looking at is skipped rather than waited for. The lease is the door's: the deadline is 300
    seconds after the claim at most, never later than the moment the platform stops waiting, and
    nothing a daemon sends moves it. The session names the outpost."""
    capacity = min(capacity, MAX_CONCURRENCY)
    if capacity <= 0 or not endpoints:
        return []
    if outpost.status != OutpostStatus.ACTIVE.value or instance.status != OutpostInstanceStatus.ACTIVE.value:
        return []
    if _TOOLS not in capabilities or _TOOLS not in (outpost.capabilities or []):
        return []
    if not get_settings().outposts_door_signing_key:
        return []  # a door that cannot sign hands nothing over: no outpost would make the call
    now = now or utcnow()
    handed: list[dict[str, str]] = []
    for call in await _unclaimed(session, outpost, endpoints, capacity, now):
        element = await _hand_over(session, outpost, instance, call, now)
        if element is not None:
            handed.append(element)
    return handed


async def _unclaimed(
    session: AsyncSession, outpost: Outpost, endpoints: list[str], capacity: int, now: datetime
) -> list[OutpostCall]:
    """The oldest calls that wait for a claim at these aliases. On PostgreSQL the rows are locked for
    this transaction, and a row another claimer has locked is skipped, not waited for."""
    waiting = await session.execute(
        select(OutpostCall)
        .where(
            OutpostCall.outpost_id == outpost.id,
            OutpostCall.state == _PENDING,
            OutpostCall.claimed_at.is_(None),
            OutpostCall.endpoint.in_(endpoints),
            OutpostCall.expires_at > now,
        )
        .order_by(OutpostCall.created_at, OutpostCall.id)
        .limit(capacity)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    return list(waiting.scalars())


async def _hand_over(
    session: AsyncSession, outpost: Outpost, instance: OutpostInstance, call: OutpostCall, now: datetime
) -> dict[str, str] | None:
    """Seal a call to the instance, sign it, and claim its row: `None` when the row was claimed since
    it was read, or when the call cannot cross the door.

    The row moves only while no instance ever claimed it (`claimed_at` is null), whatever its state
    says: a claimed write is never handed over twice (ADR 0149 §5), and the journal of the instance
    that held a read would refuse a copy under the same id."""
    if call.expires_at is None:
        return None
    deadline = min(now + timedelta(seconds=MAX_LEASE_S), _aware(call.expires_at))
    try:
        arguments = service.open_call_arguments(call)
    except SecretStoreUnavailable as lack:
        # This process cannot open what waits on the row: it holds no key wrapper, or not the one the
        # row was sealed under. The call is not handed over, here; the others are, and it says so.
        logger.error("outposts.call_not_opened", call=call.id, outpost=outpost.id, error=str(lack)[:200])
        return None
    if call.answer_key is None or (call.kind == OutpostCallKind.CALL_TOOL.value and arguments is None):
        return None  # nothing to seal: the row was emptied, it runs out of time by itself
    sealed = sealing.seal(instance.sealing_key, call.id, _plaintext(call, arguments, deadline))
    private_pem = get_settings().outposts_door_signing_key
    element = {
        "id": call.id,
        "sealed": sealed,
        "signature": sign_call(private_pem, call_id=call.id, instance_id=instance.id, sealed=sealed),
    }
    if carried_bytes(element) > MAX_SEALED_CALL_BYTES:
        await _too_large(session, call, now)
        return None
    won = await session.execute(
        update(OutpostCall)
        .where(
            OutpostCall.outpost_id == outpost.id,
            OutpostCall.id == call.id,
            OutpostCall.state == _PENDING,
            OutpostCall.claimed_at.is_(None),
        )
        .values(
            state=_CLAIMED, instance_id=instance.id, epoch=outpost.epoch, claimed_at=now, deadline=deadline
        )
        .execution_options(synchronize_session=False)
    )
    return element if won.rowcount == 1 else None  # type: ignore[attr-defined]


async def _too_large(session: AsyncSession, call: OutpostCall, now: datetime) -> None:
    """A call that weighs more than the door carries is not handed over, and does not wait for a
    lease it will never get: it is refused here, with the code an outpost gives a payload above its
    ceiling. No instance saw it, so nothing is recorded for it and it names no instance."""
    refused = await session.execute(
        update(OutpostCall)
        .where(
            OutpostCall.outpost_id == call.outpost_id,
            OutpostCall.id == call.id,
            OutpostCall.state == _PENDING,
        )
        .values(
            state=_REFUSED,
            refusal=OutpostRefusalCode.PAYLOAD_TOO_LARGE.value,
            finished_at=now,
            **_EMPTIED,
            **_NO_ANSWER_KEY,
        )
        .execution_options(synchronize_session=False)
    )
    if refused.rowcount == 1:  # type: ignore[attr-defined]
        logger.warning("outposts.call_too_large", call=call.id, outpost=call.outpost_id)
        await announce(session, call_key(call.outpost_id, call.id))


class NoSuchCall(Exception):  # noqa: N818 - the door's 404
    """No such call for this outpost. Another outpost's call answers the same: the session does not
    see it (ADR 0148 §6)."""


class LeaseLost(Exception):  # noqa: N818 - the door's 409
    """The call is not leased to this instance any more: its lease ended, the outpost's epoch moved,
    the platform stopped waiting, or another instance holds it."""


async def record(
    session: AsyncSession,
    outpost: Outpost,
    instance: OutpostInstance,
    call_id: str,
    *,
    answer: str | None = None,
    refusal: OutpostRefusalCode | None = None,
    now: datetime | None = None,
) -> str:
    """Record what an instance sends for a call, once, and return what the row holds: `answer` or
    `refusal` (ADR 0148 §9).

    It is taken from the instance that claimed the call, under the outpost's epoch of the claim,
    before the deadline, by one compare-and-set while nothing is recorded: the first of an answer
    and a refusal wins. The same instance sending again under the same epoch reads what was
    recorded, whatever it sends and whatever the row's state has become since. The answer is stored
    as it came, sealed: the door does not open it. The session names the outpost."""
    if (answer is None) == (refusal is None):
        raise ValueError("an answer or a refusal, one of them")
    now = now or utcnow()
    call = (
        await session.execute(
            select(OutpostCall).where(OutpostCall.outpost_id == outpost.id, OutpostCall.id == call_id)
        )
    ).scalar_one_or_none()
    if call is None:
        raise NoSuchCall(call_id)
    took: dict[str, Any] = (
        {"recorded": "answer", "state": _ANSWERED, "answer_sealed": answer}
        if refusal is None
        else {"recorded": "refusal", "state": _REFUSED, "refusal": refusal.value, **_NO_ANSWER_KEY}
    )
    done = await session.execute(
        update(OutpostCall)
        .where(
            OutpostCall.outpost_id == outpost.id,
            OutpostCall.id == call_id,
            OutpostCall.recorded.is_(None),
            OutpostCall.state == _CLAIMED,
            OutpostCall.instance_id == instance.id,
            # The epoch as it is when this statement runs: a revocation that committed since the
            # outpost was read moved it, and nothing in flight is taken under the former one.
            OutpostCall.epoch == select(Outpost.epoch).where(Outpost.id == outpost.id).scalar_subquery(),
            OutpostCall.deadline > now,
        )
        .values(
            recorded_by=instance.id,
            recorded_epoch=OutpostCall.epoch,
            recorded_at=now,
            finished_at=now,
            **took,
            **_EMPTIED,
        )
        .execution_options(synchronize_session=False)
    )
    if done.rowcount == 1:  # type: ignore[attr-defined]
        await announce(session, call_key(outpost.id, call_id))
        return str(took["recorded"])
    await session.refresh(call)
    if call.recorded is not None and call.recorded_by == instance.id and call.recorded_epoch == outpost.epoch:
        return call.recorded
    raise LeaseLost(call_id)


async def no_longer_awaited(
    session: AsyncSession,
    outpost: Outpost,
    instance: OutpostInstance,
    running: list[str],
    now: datetime | None = None,
) -> list[str]:
    """Among the calls an instance says it holds, those the door waits for no more: an answer to one
    would not be taken (the poll's `cancel`). A call this instance still leases is awaited, and so is
    one it already answered, whose answer it may send again. The session names the outpost."""
    if not running:
        return []
    now = now or utcnow()
    rows = await session.execute(
        select(
            OutpostCall.id,
            OutpostCall.state,
            OutpostCall.instance_id,
            OutpostCall.epoch,
            OutpostCall.deadline,
            OutpostCall.recorded_by,
            OutpostCall.recorded_epoch,
        ).where(OutpostCall.outpost_id == outpost.id, OutpostCall.id.in_(running))
    )
    awaited: set[str] = set()
    for call_id, state, holder, epoch, deadline, recorded_by, recorded_epoch in rows:
        leased = (
            state == _CLAIMED
            and holder == instance.id
            and epoch == outpost.epoch
            and deadline is not None
            and _aware(deadline) > now
        )
        if leased or (recorded_by == instance.id and recorded_epoch == outpost.epoch):
            awaited.add(str(call_id))
    return [call_id for call_id in dict.fromkeys(running) if call_id not in awaited]


# ───────────────────────── leases that end ─────────────────────────


async def _close(
    session: AsyncSession,
    outpost_id: str,
    now: datetime,
    *,
    call_id: str | None = None,
    whatever_the_time: bool = False,
) -> int:
    """Close the calls of an outpost that ran out of time, or one call whatever the time (a cancel).

    An unclaimed call is `expired`: nothing reached any server. A claimed read is `timed_out`. A
    claimed write is `in_doubt`: nobody knows whether the outpost's server executed it, and it is
    never leased again (ADR 0149 §5). Each is a compare-and-set on the state it leaves: an answer
    recorded in the meantime wins. What the row held while it waited goes. The session names the
    outpost. Returns how many rows moved."""
    moved = 0
    for was, write, becomes, clock in (
        (_PENDING, None, _EXPIRED, OutpostCall.expires_at),
        (_CLAIMED, False, _TIMED_OUT, OutpostCall.deadline),
        (_CLAIMED, True, _IN_DOUBT, OutpostCall.deadline),
    ):
        conditions = [OutpostCall.outpost_id == outpost_id, OutpostCall.state == was]
        if write is not None:
            conditions.append(OutpostCall.write.is_(write))
        if call_id is not None:
            conditions.append(OutpostCall.id == call_id)
        if not whatever_the_time:
            conditions.append(clock <= now)
        done = await session.execute(
            update(OutpostCall)
            .where(*conditions)
            .values(state=becomes, finished_at=now, **_EMPTIED, **_NO_ANSWER_KEY)
            .execution_options(synchronize_session=False)
        )
        moved += int(done.rowcount or 0)  # type: ignore[attr-defined]
    return moved


async def close_what_ran_out(session: AsyncSession, outpost_id: str, now: datetime) -> int:
    """`expire` for a session that already names the outpost: the door's, at a poll's first look."""
    return await _close(session, outpost_id, now)


async def expire(session: AsyncSession, outpost_id: str, *, now: datetime | None = None) -> int:
    """Close what ran out of time at an outpost: an unclaimed call is `expired`, a claimed read
    `timed_out`, a claimed write `in_doubt`. The outpost is named here; returns how many moved."""
    async with in_outpost_scope(session, outpost_id):
        return await _close(session, outpost_id, now or utcnow())


async def cancel(
    session: AsyncSession, outpost_id: str, call_id: str, *, now: datetime | None = None
) -> OutpostCallState | None:
    """The platform stops waiting for a call, and says so to the instance that holds it at its next
    poll. Returns the state the call is left in, `None` when there is no such call.

    A cancel does not interrupt a write in flight: a claimed write is `in_doubt` from here on, and
    its late answer meets `lease_lost`. A call nobody claimed is `expired`, a claimed read
    `timed_out`; one that was already answered or refused stays what it is."""
    now = now or utcnow()
    async with in_outpost_scope(session, outpost_id):
        if await _close(session, outpost_id, now, call_id=call_id, whatever_the_time=True):
            await announce(session, calls_key(outpost_id))
            await announce(session, call_key(outpost_id, call_id))
        state = (
            await session.execute(
                select(OutpostCall.state).where(
                    OutpostCall.outpost_id == outpost_id, OutpostCall.id == call_id
                )
            )
        ).scalar_one_or_none()
    return OutpostCallState(state) if state is not None else None


# ───────────────────────── the platform's side: reading what was recorded ─────────────────────────


@dataclass(frozen=True, slots=True)
class Outcome:
    """What became of a call, as whoever placed it reads it."""

    id: str
    state: OutpostCallState
    write: bool
    #: The instance that claimed it and when: what an in-doubt write is shown with (ADR 0149 §5).
    instance_id: str | None = None
    claimed_at: datetime | None = None
    #: A refused call: the code, which names a class of refusal and never the rule.
    refusal: OutpostRefusalCode | None = None
    #: An answered call, its envelope open. `None` once the row was emptied (`forget`).
    answer: OutpostAnswer | None = None
    #: When the platform stops waiting, while it still does.
    until: datetime | None = None

    @property
    def waiting(self) -> bool:
        """Nothing is known yet: the call waits for a claim or for its answer."""
        return self.state in (OutpostCallState.PENDING, OutpostCallState.CLAIMED)


def _digest(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _opened(call: OutpostCall) -> OutpostAnswer | None:
    """The answer of a call, opened with the private half of its answer key and read as the contract
    describes it; `None` when it does not open, is not an answer, or answers another call."""
    private = _answer_private_key(call)
    if private is None or call.answer_sealed is None:
        return None
    try:
        answer = OutpostAnswer.model_validate(sealing.open_sealed(private, call.id, call.answer_sealed))
    except (sealing.SealError, ValidationError):
        return None
    return answer if answer.id == call.id else None


async def _read(session: AsyncSession, outpost_id: str, call_id: str) -> Outcome | None:
    call = (
        await session.execute(
            select(OutpostCall)
            .where(OutpostCall.outpost_id == outpost_id, OutpostCall.id == call_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if call is None:
        return None
    answer: OutpostAnswer | None = None
    if call.recorded == "answer" and call.answer_sealed is not None:
        answer = _opened(call)
        if call.outcome is None:
            # The first reader writes what the door could not know: it stored the envelope unopened.
            unanswered = answer is None or answer.outcome is OutpostAnswerOutcome.UNANSWERED
            if answer is None:
                logger.warning("outposts.answer_unreadable", call=call.id, outpost=outpost_id)
            call.outcome = (OutpostAnswerOutcome.UNANSWERED if answer is None else answer.outcome).value
            if answer is not None and answer.outcome is OutpostAnswerOutcome.RESULT:
                shown = (
                    answer.result if answer.result is not None else answer.model_dump(mode="json")["tools"]
                )
                call.result_sha256 = _digest(shown)
                call.is_error = bool((answer.result or {}).get("isError", False))
            elif answer is not None and answer.outcome is OutpostAnswerOutcome.ERROR:
                call.is_error = True
            if unanswered:
                # The call was sent, or begun, and the outpost holds no answer: executed or not, nobody
                # knows. The state says so; the record of what the door took does not move.
                call.state = _IN_DOUBT if call.write else _TIMED_OUT
            await session.flush()
    until = call.deadline if call.state == _CLAIMED else call.expires_at if call.state == _PENDING else None
    return Outcome(
        id=call.id,
        state=OutpostCallState(call.state),
        write=bool(call.write),
        instance_id=call.instance_id,
        claimed_at=_aware(call.claimed_at) if call.claimed_at is not None else None,
        refusal=OutpostRefusalCode(call.refusal) if call.refusal else None,
        answer=answer if call.state == _ANSWERED else None,
        until=_aware(until) if until is not None else None,
    )


async def read(session: AsyncSession, outpost_id: str, call_id: str) -> Outcome | None:
    """What became of a call, without waiting; `None` when there is no such call. An answer is opened
    here, with the private half of the call's answer key, and what the door could not know of it is
    written on the row: its outcome and the digest of its result. An answer that says `unanswered`,
    or does not open, leaves a write `in_doubt` and a read `timed_out`. The outpost is named here."""
    async with in_outpost_scope(session, outpost_id):
        return await _read(session, outpost_id, call_id)


async def wait(outpost_id: str, call_id: str) -> Outcome | None:
    """Wait until a call is answered, refused or out of time, and return what became of it; `None`
    when there is no such call, or no such outpost any more.

    It holds no transaction and no pooled connection while it waits: each look is a short transaction
    of its own (`in_scope`). It is woken when the door records something for the call, and looks again
    periodically whatever it heard. When the call's time is up it closes it itself: unclaimed it is
    `expired`, a claimed read is `timed_out`, a claimed write `in_doubt`."""
    with _waiters.on(call_key(outpost_id, call_id)) as moved:
        while True:
            moved.clear()
            async with in_scope(outpost_id) as scope:
                if scope is None:
                    return None
                now = utcnow()
                outcome = await _read(scope.session, outpost_id, call_id)
                if (
                    outcome is not None
                    and outcome.waiting
                    and outcome.until is not None
                    and outcome.until <= now
                ):
                    await _close(scope.session, outpost_id, now, call_id=call_id)
                    outcome = await _read(scope.session, outpost_id, call_id)
            if outcome is None or not outcome.waiting:
                return outcome
            pause = random.uniform(*LOOK_S)  # noqa: S311 - jitter, not a secret
            if outcome.until is not None:
                pause = min(pause, max(0.0, (outcome.until - utcnow()).total_seconds()) + 0.05)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(moved.wait(), timeout=pause)


async def forget(session: AsyncSession, outpost_id: str, call_id: str) -> None:
    """Empty what is left of a call once whoever placed it has what it needs: the sealed answer, the
    private half of the answer key, and whatever of the arguments was still there. The digests, the
    claim and the record stay (ADR 0148 §11). The outpost is named here."""
    async with in_outpost_scope(session, outpost_id):
        await session.execute(
            update(OutpostCall)
            .where(
                OutpostCall.outpost_id == outpost_id,
                OutpostCall.id == call_id,
                OutpostCall.state.not_in(_WAITING),
            )
            .values(answer_sealed=None, **_EMPTIED, **_NO_ANSWER_KEY)
            .execution_options(synchronize_session=False)
        )


# ───────────────────────── the platform's sweep ─────────────────────────


async def sweep(now: datetime | None = None) -> int:
    """Close what ran out of time at every outpost, and empty the answers nobody came to read.

    The polls and the placements already do it for the outposts that are polled or called; this is
    for the others: an outpost that went away with calls in flight. It runs as the platform. Under
    that scope no outpost's row exists until an outpost is named (ADR 0148 §6), so the registry is
    listed first and each outpost is named in turn, in a transaction of its own."""
    async with session_scope(orgs=ALL_ORGS) as session:
        outposts = list((await session.execute(select(Outpost.id))).scalars())
    moved = 0
    for outpost_id in outposts:
        async with in_scope(outpost_id) as scope:
            if scope is None:
                continue
            at = now or utcnow()
            moved += await _close(scope.session, outpost_id, at)
            await scope.session.execute(
                update(OutpostCall)
                .where(
                    OutpostCall.outpost_id == outpost_id,
                    OutpostCall.answer_sealed.is_not(None),
                    OutpostCall.finished_at <= at - timedelta(seconds=ANSWER_KEPT_S),
                )
                .values(answer_sealed=None, **_NO_ANSWER_KEY)
                .execution_options(synchronize_session=False)
            )
    return moved


async def sweep_loop(interval: float | None = None) -> None:
    """`sweep`, for as long as the process lives. A pass that fails is logged and tried again."""
    while True:
        try:
            await sweep()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("outposts.sweep_failed", error=f"{type(error).__name__}: {error}"[:300])
        await asyncio.sleep(SWEEP_S if interval is None else interval)


__all__ = [
    "ANSWER_KEPT_S",
    "CHANNEL",
    "ID_TAKEN",
    "LOOK_S",
    "MAX_SEALED_CALL_BYTES",
    "ORGANISATION_QUOTA",
    "OUTPOST_QUOTA",
    "PAUSED",
    "REVOKED",
    "SWITCHED_OFF",
    "TOO_LARGE",
    "LeaseLost",
    "NoSuchCall",
    "NotAdmitted",
    "Outcome",
    "Placed",
    "Quota",
    "Scope",
    "Waiters",
    "announce",
    "call_id_of",
    "call_key",
    "calls_key",
    "cancel",
    "carried_bytes",
    "claim",
    "close_what_ran_out",
    "expire",
    "forget",
    "in_scope",
    "new_call_id",
    "no_longer_awaited",
    "place",
    "read",
    "record",
    "sign_call",
    "sweep",
    "sweep_loop",
    "wait",
    "waiters",
    "woken",
]
