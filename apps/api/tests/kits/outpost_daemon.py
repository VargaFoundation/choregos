"""A daemon as the door's tests need one, and the platform's side of a call, written by hand.

The real daemon (`choregos-outpost`) is another package. This one is scripted: it makes its keys on
the spot, signs each request as `packages/contracts/outpost-door.yaml` says (`x-request-signature`),
keeps the last nonce the door sent and raises its sequence from its clock in milliseconds, and it
can be made to sign wrongly, one claim at a time. It checks a call the door hands over as the
contract tells a daemon to (`x-call-signature.checks`) before it opens the envelope.

No key is written here: the daemon's are generated, the door's come from `kits.outposts.switch_on`,
and the deployment's key wrapper is derived from a label.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

import choregos_contracts as contracts
import jwt
import pytest
from choregos_adapters import sealing
from choregos_contracts import NONCE_HEADER, SIGNATURE_HEADER, outpost
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from httpx import AsyncClient, Response
from jsonschema import Draft202012Validator
from sqlalchemy import select

from . import outposts as kit

DOOR = "/outposts/v1"
ENROL, POLL = f"{DOOR}/enrol", f"{DOOR}/poll"
SLUG = "factory-floor"
#: What the deployment's key wrapper is derived from in these tests: a label, never a key.
KEY_WRAPPER_LABEL = "choregos tests: the key wrapper a call's arguments wait under"

_RAW = (serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def key_wrapper(monkeypatch: pytest.MonkeyPatch, label: str = KEY_WRAPPER_LABEL) -> None:
    """Give the deployment a key wrapper (ADR 0103): without one no call's arguments are written."""
    from choregos_api.secret_store import ENV_KEKS

    key = base64.b64encode(hashlib.sha256(label.encode()).digest()).decode()
    monkeypatch.setenv(ENV_KEKS, f"k1:{key}")


def compact(body: Any) -> bytes:
    return json.dumps(body, separators=(",", ":")).encode()


def answer_path(call_id: str) -> str:
    return f"{DOOR}/calls/{call_id}/answer"


def refuse_path(call_id: str) -> str:
    return f"{DOOR}/calls/{call_id}/refuse"


# ───────────────────────── the contract's documents ─────────────────────────


def errors_against(schema: dict[str, Any], body: Any) -> list[str]:
    """What `body` does not respect of a schema, the door's components at hand for its `$ref`s."""
    document = {"components": contracts.load_outpost_door()["components"], **schema}
    validator = Draft202012Validator(document, format_checker=Draft202012Validator.FORMAT_CHECKER)
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in validator.iter_errors(body)]


def is_a(component: str, body: Any) -> None:
    """The body is what the door's document describes under that name (`components.schemas`)."""
    schema = contracts.load_outpost_door()["components"]["schemas"][component]
    errors = errors_against(schema, body)
    assert not errors, f"not the contract's `{component}`: {errors}"


def is_the_schema(name: str, body: Any) -> None:
    """The body is what one of the contract's schema files describes."""
    errors = errors_against(contracts.load_schema(name), body)
    assert not errors, f"not the contract's `{name}`: {errors}"


def refused(response: Response, code: str, status: int) -> dict[str, Any]:
    """The answer is the contract's problem of that code: its status, its shape, the door's time."""
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = dict(response.json())
    assert body["code"] == code, body
    is_a("DoorProblem", body)
    assert outpost.DOOR_STATUS[outpost.OutpostDoorCode(code)] == status
    assert body["instance"].startswith(DOOR) and body["status"] == status
    return body


# ───────────────────────── the daemon ─────────────────────────

_UNSET: Any = object()


@dataclass
class Daemon:
    """One instance: its keys, the nonce it holds and the sequence it raises."""

    signing: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)
    sealing: X25519PrivateKey = field(default_factory=X25519PrivateKey.generate)
    instance_id: str | None = None
    nonce: str | None = None
    sequence: int = 0
    origin: str = kit.ORIGIN

    @property
    def signing_key(self) -> str:
        return b64(self.signing.public_key().public_bytes(*_RAW))

    @property
    def sealing_key(self) -> str:
        return b64(self.sealing.public_key().public_bytes(*_RAW))

    def next_sequence(self) -> int:
        """As the daemon numbers them: from its clock in milliseconds, never twice the same."""
        self.sequence = max(self.sequence + 1, int(time.time() * 1000))
        return self.sequence

    def sign(
        self,
        path: str,
        body: bytes,
        *,
        method: str = "POST",
        url: str | None = None,
        iat: int | None = None,
        nonce: Any = _UNSET,
        seq: Any = _UNSET,
        kid: Any = _UNSET,
        header: dict[str, Any] | None = None,
        claims: dict[str, Any] | None = None,
        signer: Ed25519PrivateKey | None = None,
    ) -> str:
        """A request's signature. With nothing bent, the one the contract describes; an enrolment's
        (`kid=None`) carries no `kid`, nonce or sequence."""
        kid = self.instance_id if kid is _UNSET else kid
        session = kid is not None
        if nonce is _UNSET:
            nonce = self.nonce if session else None
        if seq is _UNSET:
            seq = self.next_sequence() if session else None
        signed = outpost.request_signature_claims(
            method=method,
            url=url if url is not None else f"{self.origin}{path}",
            body=body,
            iat=int(time.time()) if iat is None else iat,
            nonce=nonce,
            seq=seq,
        )
        return jwt.encode(
            signed | (claims or {}),
            signer or self.signing,
            algorithm=outpost.SIGNATURE_ALGORITHM,
            headers=outpost.request_signature_header(kid) | (header or {}),
        )

    async def send(
        self, client: AsyncClient, path: str, body: Any, *, signature: Any = _UNSET, **bent: Any
    ) -> Response:
        """Send a signed request and keep the nonce the answer carries. `body` is sent as it is when
        it is bytes; `signature=None` sends none, any other value is sent in its place."""
        raw = body if isinstance(body, bytes) else compact(body)
        if signature is _UNSET:
            signature = self.sign(path, raw, **bent)
        headers = {"content-type": "application/json"}
        if signature is not None:
            headers[SIGNATURE_HEADER] = signature
        response = await client.post(path, content=raw, headers=headers)
        if NONCE_HEADER in response.headers:
            self.nonce = response.headers[NONCE_HEADER]
        return response

    def enrolment(self, token: str, **changes: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "protocol": 1,
            "token": token,
            "signing_key": self.signing_key,
            "sealing_key": self.sealing_key,
            "daemon_version": "0.27.0",
            "capabilities": ["tools"],
            "facts": {"hostname": "outpost-0.tools.svc", "platform": "linux/amd64"},
        }
        return {name: value for name, value in (body | changes).items() if value is not _UNSET}

    async def enrol(self, client: AsyncClient, token: str, **bent: Any) -> Response:
        """`POST /outposts/v1/enrol`, signed with the key it enrols; the instance's id is kept."""
        response = await self.send(client, ENROL, self.enrolment(token), kid=None, **bent)
        if response.status_code == 201:
            self.instance_id = response.json()["instance_id"]
        return response

    def poll_body(self, **changes: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "protocol": 1,
            "daemon_version": "0.27.0",
            "wait_s": 0,
            "capacity": 4,
            "capabilities": ["tools"],
            "endpoints": ["tracker"],
            "running": [],
        }
        return {name: value for name, value in (body | changes).items() if value is not _UNSET}

    async def poll(self, client: AsyncClient, **changes: Any) -> Response:
        return await self.send(client, POLL, self.poll_body(**changes))

    async def calls(self, client: AsyncClient, **changes: Any) -> list[dict[str, Any]]:
        """Poll, and return the calls handed over, each checked and opened as a daemon does."""
        response = await self.poll(client, **changes)
        assert response.status_code == 200, response.text
        return [self.opened(handed) for handed in response.json()["calls"]]

    def verifies(self, handed: dict[str, Any], pinned: list[str]) -> bool:
        """What `x-call-signature.checks` asks of a daemon before it opens an envelope, in its order:
        the header is the contract's and its `kid` is a pinned key, the signature verifies under that
        key, and its claims are this call, this instance, this envelope, and no other."""
        signature = handed.get("signature")
        if not isinstance(signature, str):
            return False
        try:
            header = jwt.get_unverified_header(signature)
        except jwt.InvalidTokenError:
            return False
        key = header.get("kid")
        if header != outpost.call_signature_header(str(key)) or key not in pinned:
            return False
        try:
            claims = jwt.decode(
                signature,
                Ed25519PublicKey.from_public_bytes(unb64(str(key))),
                algorithms=[outpost.SIGNATURE_ALGORITHM],
            )
        except jwt.InvalidTokenError:
            return False
        expected = outpost.call_signature_claims(
            call_id=handed["id"], instance_id=str(self.instance_id), sealed=handed["sealed"]
        )
        return bool(claims == expected)

    def opened(self, handed: dict[str, Any], pinned: list[str] | None = None) -> dict[str, Any]:
        """The call inside an element of a poll's `calls`: verified under the door's key in service,
        then opened with this instance's sealing key, and what the contract says a call is."""
        from choregos_api.config import get_settings

        is_a("SealedCall", handed)
        pinned = get_settings().outposts_door_keys[:1] if pinned is None else pinned
        assert self.verifies(handed, pinned), "the door's signature of this call does not verify"
        call = sealing.open_sealed(self.sealing, handed["id"], handed["sealed"])
        is_the_schema("outpost-call.schema.json", call)
        assert call["id"] == handed["id"]
        outpost.OutpostCall.model_validate(call)
        return call

    def sealed_answer(self, call: dict[str, Any], **answer: Any) -> str:
        """An answer sealed to the `answer_key` found inside the call, as the contract's schema has it."""
        body: dict[str, Any] = {"id": call["id"], "outcome": "result"}
        if "outcome" not in answer and "result" not in answer and "tools" not in answer:
            body["result"] = {"content": [{"type": "text", "text": "done"}], "isError": False}
        body |= answer
        is_the_schema("outpost-answer.schema.json", body)
        return sealing.seal(call["answer_key"], call["id"], body)

    async def answer(self, client: AsyncClient, call: dict[str, Any], **answer: Any) -> Response:
        sealed = {"sealed": self.sealed_answer(call, **answer)}
        return await self.send(client, answer_path(call["id"]), sealed)

    async def refuse(self, client: AsyncClient, call_id: str, code: str = "tool_not_allowed") -> Response:
        return await self.send(client, refuse_path(call_id), {"code": code})


async def enrolled(client: AsyncClient, slug: str = SLUG) -> Daemon:
    """A daemon enrolled through the door at an outpost that exists: pending, with its first nonce."""
    daemon = Daemon()
    response = await daemon.enrol(client, await kit.token_for(client, slug))
    assert response.status_code == 201, response.text
    assert daemon.nonce, "the enrolment's answer carries the nonce of the first signed request"
    return daemon


async def approved(client: AsyncClient, slug: str = SLUG, *, create: bool = True) -> Daemon:
    """A daemon whose instance a person approved: it may poll."""
    if create:
        await kit.create(client, slug)
    daemon = await enrolled(client, slug)
    await kit.approve(client, slug, str(daemon.instance_id))
    return daemon


# ───────────────────────── the platform's side ─────────────────────────


async def place(slug: str = SLUG, *, scope: Any = None, **call: Any) -> Any:
    """Place a call as whoever routes one to an outpost will (S65-13), in a transaction of its own
    under the organisation's scope; `scope="*"` places it as a run's token would."""
    from choregos_api.db.models import Organization, Outpost
    from choregos_api.db.session import session_scope
    from choregos_api.outposts import rendezvous

    arguments: dict[str, Any] = {
        "call_id": rendezvous.new_call_id(),
        "kind": "call_tool",
        "endpoint": "tracker",
        "tool": "search_issues",
        "arguments": {"query": "door stuck", "limit": 5},
        "timeout_s": 30,
    } | call
    if arguments["kind"] == "list_tools":
        arguments |= {"tool": None, "arguments": None} | {k: v for k, v in call.items() if k in ("tool",)}
    async with session_scope(orgs=scope or [kit.ORG]) as session:
        outpost_row = (
            await session.execute(
                select(Outpost)
                .join(Organization, Organization.id == Outpost.org_id)
                .where(Organization.slug == kit.ORG, Outpost.slug == slug)
            )
        ).scalar_one()
        return await rendezvous.place(session, outpost_row, **arguments)


async def call_row(call_id: str, slug: str = SLUG) -> Any:
    from choregos_api.db.models import OutpostCall

    rows = [row for row in await kit.rows_of(slug, OutpostCall) if row.id == call_id]
    assert len(rows) == 1, f"{len(rows)} rows for the call {call_id}"
    return rows[0]


async def instance_row(instance_id: str, slug: str = SLUG) -> Any:
    from choregos_api.db.models import OutpostInstance

    (row,) = [row for row in await kit.rows_of(slug, OutpostInstance) if row.id == instance_id]
    return row


async def change(model: Any, row_id: str, slug: str = SLUG, **values: Any) -> None:
    """Write a row of an outpost by hand: what time, a crash or another replica would have done."""
    from choregos_api.db.session import name_the_outpost, session_scope
    from sqlalchemy import update

    outpost_row = await kit.outpost_row(slug)
    async with session_scope(orgs="*") as session:
        await name_the_outpost(session, outpost_row.id)
        done = await session.execute(update(model).where(model.id == row_id).values(**values))
        assert done.rowcount == 1  # type: ignore[attr-defined]


async def instances(count: int, slug: str = SLUG) -> list[Daemon]:
    """Approved instances of an outpost, written by hand with the keys of as many daemons: what a
    person approving them one by one would leave. Each daemon holds a nonce of the door."""
    import time as clock

    from choregos_api.config import get_settings
    from choregos_api.db.base import uuid7
    from choregos_api.db.models import OutpostInstance
    from choregos_api.db.session import name_the_outpost, session_scope
    from choregos_api.outposts import auth

    outpost_row = await kit.outpost_row(slug)
    nonce = auth.nonces(get_settings()).issue(at=clock.time())
    daemons: list[Daemon] = []
    async with session_scope(orgs="*") as session:
        await name_the_outpost(session, outpost_row.id)
        for _ in range(count):
            daemon = Daemon(nonce=nonce)
            daemon.instance_id = outpost_row.id.replace("-", "") + uuid7().replace("-", "")
            session.add(
                OutpostInstance(
                    id=daemon.instance_id,
                    org_id=outpost_row.org_id,
                    outpost_id=outpost_row.id,
                    status="active",
                    signing_key=daemon.signing_key,
                    sealing_key=daemon.sealing_key,
                    fingerprint=outpost.fingerprint_of(daemon.signing_key),
                    daemon_version="0.27.0",
                    capabilities=["tools"],
                    endpoints=[],
                )
            )
            daemons.append(daemon)
        await session.flush()
    return daemons
