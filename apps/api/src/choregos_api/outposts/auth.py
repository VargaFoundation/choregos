# SPDX-License-Identifier: Apache-2.0
"""Who is at the outposts' door: a request is authenticated by a signature that binds it (ADR 0148 §4).

Every request an outpost sends carries `Outpost-Signature`: a compact JWS made with the Ed25519 key
the instance enrolled, which binds the method, the URL, a digest of the body, an `iat`, a nonce the
door issued and a sequence the instance raises. Nothing else opens this door: no session, no API
token, no OAuth token; and this signature opens no other door (`deps.presents_outpost_signature`).

A request that fails several checks is answered for the first that fails, in the order the contract
gives (`outpost-door.yaml`, `x-request-signature.checks`):

1. the body's size, on the bytes read and before anything is parsed (413);
2. the signature: absent, malformed, made by a key the door does not know, or not verifying. The
   door does not say which (401 `invalid_signature`);
3. what it binds: the method, the URL under the door's public origin, the SHA-256 of the body as
   it was sent (401 `request_mismatch`);
4. `iat`, 60 seconds either way from the door's clock (401 `stale_iat`);
5. the nonce (401 `stale_nonce`, which carries a fresh one, as every 401 does);
6. the sequence, kept on the instance's row and taken from there on (401 `replayed_sequence`);
7. the instance: approved, and neither it nor its outpost revoked (403);
8. the rate, per instance (429);
9. the protocol, read from the body before the rest of it is judged (426);
10. the body against its schema (422);

then what the verb itself refuses. An enrolment has no nonce, no sequence and no instance yet: its
signature is made with the key it enrols, read from the body, and the token is checked where the
instance would be.

The door resolves a signature to one instance of one outpost of one organisation with S65-11's
`scope_to_outpost` and never works under the all-organisations scope (ADR 0148 §6). Bodies are
parsed here by hand: a problem the framework would write for an invalid body has no `code` and no
`server_time`, and every problem of this door is the contract's `DoorProblem`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Final

import jwt
from choregos_contracts import (
    DOOR_STATUS,
    IAT_WINDOW_S,
    NONCE_HEADER,
    NONCE_TTL_S,
    OUTPOST_PROTOCOL,
    SIGNATURE_ALGORITHM,
    SIGNATURE_HEADER,
    OutpostDoorCode,
    OutpostInstanceStatus,
    OutpostStatus,
    request_signature_header,
)
from choregos_contracts.outpost import OUTPOST_ID_PATTERN
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import ClientDisconnect

from ..config import Settings
from ..db.models import Outpost, OutpostInstance
from ..db.session import release_connection
from ..errors import PROBLEM_JSON, ApiError
from ..rate_limit import RateLimiter
from ..rbac import Principal, outpost_principal
from ..services import outposts as service

#: The protocol window this door serves (ADR 0148 §3): a daemon below the minimum is refused, cleanly,
#: with the window, and handed nothing.
PROTOCOL_CURRENT: int = OUTPOST_PROTOCOL
PROTOCOL_MINIMUM: int = 1
#: A sequence is a whole number the instance raises; a daemon numbers it from its clock in
#: milliseconds, and the row keeps it on 64 bits.
MAX_SEQUENCE: Final = 2**63 - 1
#: The answers that carry a nonce to sign the next request with (`outpost-door.yaml`, the headers of
#: its answers): every answer that is taken, and every refusal a daemon signs again after.
NONCE_STATUSES: Final = frozenset({200, 201, 401, 403, 404, 409})
_NONCE_INFO: Final = b"choregos/outpost-nonce/v1"
_NONCE_VERSION: Final = "v1"
_TITLES: Final = {
    401: "Not authenticated",
    403: "Forbidden",
    404: "Not found",
    409: "Conflict",
    413: "Body too large",
    422: "Invalid request",
    426: "Upgrade required",
    429: "Too many requests",
}
_SESSION_REFUSALS: Final = {
    OutpostInstanceStatus.PENDING.value: (
        OutpostDoorCode.INSTANCE_PENDING,
        "nobody has approved this instance yet: it is handed nothing until its fingerprint is compared",
    ),
    OutpostInstanceStatus.REVOKED.value: (
        OutpostDoorCode.INSTANCE_REVOKED,
        "this instance was revoked: its signature opens nothing any more",
    ),
}


def clock() -> float:
    """The door's clock, in seconds: what `iat` and a nonce are judged against."""
    return time.time()


def server_time() -> str:
    """The door's clock as every answer and every problem carries it: a daemon whose own drifts
    learns it there. It is not signed, and a daemon decides nothing on it."""
    return datetime.fromtimestamp(int(clock()), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def window() -> dict[str, int]:
    return {"current": PROTOCOL_CURRENT, "minimum": PROTOCOL_MINIMUM}


# ───────────────────────── a problem of the door ─────────────────────────


class Refused(Exception):  # noqa: N818 - a decision of the door, not a failure
    """The door refuses a request with one of the contract's codes. What was written before the
    refusal stays written: the sequence a request used is taken whatever is refused next."""

    def __init__(
        self,
        code: OutpostDoorCode,
        detail: str,
        *,
        retry_after: int | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.detail = detail
        self.retry_after = retry_after
        self.errors = errors


def problem(request: Request, settings: Settings, refused: Refused) -> JSONResponse:
    """The contract's `DoorProblem` (RFC 9457) for a refusal: its `code`, the door's `server_time`,
    and what the status asks for: a fresh nonce, the `Upgrade` of a 426, the `Retry-After` of a 429."""
    status = DOOR_STATUS[refused.code]
    extra: dict[str, Any] = {"code": refused.code.value, "server_time": server_time()}
    if refused.code is OutpostDoorCode.PROTOCOL_UNSUPPORTED:
        extra["protocol"] = window()
    body = ApiError(
        status,
        _TITLES[status],
        refused.detail,
        errors=refused.errors,
        retry_after=refused.retry_after,
        extra=extra,
    ).to_problem(str(request.scope["path"]))
    headers: dict[str, str] = {}
    if status in NONCE_STATUSES:
        headers[NONCE_HEADER] = nonces(settings).issue()
    if refused.code is OutpostDoorCode.PROTOCOL_UNSUPPORTED:
        headers["Upgrade"] = f"choregos-outpost/{PROTOCOL_MINIMUM}"
    if refused.retry_after is not None:
        headers["Retry-After"] = str(refused.retry_after)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def answer(settings: Settings, body: dict[str, Any], status: int = 200) -> JSONResponse:
    """An answer the door gives, with the nonce the daemon signs its next request with."""
    return JSONResponse(body, status_code=status, headers={NONCE_HEADER: nonces(settings).issue()})


# ───────────────────────── the nonce ─────────────────────────


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Nonces:
    """The door's nonces: an authenticated timestamp it checks alone, with nothing stored (RFC 9449
    §8, ADR 0148 §4). `v1.<seconds>.<MAC of the two>`. A nonce proves freshness; it is not single
    use: what refuses a replay is the sequence."""

    def __init__(self, key: bytes) -> None:
        self._key = key

    def _mac(self, issued: int) -> str:
        message = f"{_NONCE_VERSION}.{issued}".encode()
        return _b64(hmac.new(self._key, message, hashlib.sha256).digest())

    def issue(self, at: float | None = None) -> str:
        issued = int(clock() if at is None else at)
        return f"{_NONCE_VERSION}.{issued}.{self._mac(issued)}"

    def fresh(self, nonce: object, at: float | None = None) -> bool:
        """Whether this door issued the nonce, and no more than 300 seconds ago. One a replica whose
        clock runs ahead issued is taken too, within the window an `iat` is."""
        if not isinstance(nonce, str) or len(nonce) > 256:
            return False
        version, _, rest = nonce.partition(".")
        seconds, _, mac = rest.partition(".")
        if version != _NONCE_VERSION or not seconds.isascii() or not seconds.isdigit() or len(seconds) > 12:
            return False
        issued = int(seconds)
        if not hmac.compare_digest(mac.encode(), self._mac(issued).encode()):
            return False
        age = (clock() if at is None else at) - issued
        return -IAT_WINDOW_S <= age <= NONCE_TTL_S


@lru_cache(maxsize=4)
def _nonce_key(private_pem: str) -> bytes:
    """The key the nonces are authenticated with, derived from the door's signing key: every replica
    that holds the one holds the other, and the door has no further secret. It rotates with the
    signing key: a nonce issued under the former key is stale, and the refusal that says so carries a
    fresh one, so a daemon signs again and nothing else is needed."""
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    if not isinstance(key, Ed25519PrivateKey):  # the settings already refused it at start-up
        raise ValueError("the door's signing key is not an Ed25519 key")
    seed = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_NONCE_INFO).derive(seed)


def nonces(settings: Settings) -> Nonces:
    return Nonces(_nonce_key(settings.outposts_door_signing_key))


# ───────────────────────── the body, read by hand ─────────────────────────


async def read_body(request: Request, cap: int) -> bytes:
    """The body as it was sent, or a 413 as soon as more than the verb's cap was read: the count is
    of the bytes received, not of a header, and nothing is parsed before it (ADR 0148 §9)."""
    received = bytearray()
    try:
        async for chunk in request.stream():
            received += chunk
            if len(received) > cap:
                raise Refused(
                    OutpostDoorCode.BODY_TOO_LARGE, f"the body is above this verb's cap of {cap} bytes"
                )
    except ClientDisconnect:
        raise Refused(OutpostDoorCode.INVALID_REQUEST, "the body did not arrive whole") from None
    return bytes(received)


def document_of(raw: bytes) -> dict[str, Any] | None:
    """The body as a JSON object, read leniently for the two things that are looked at before its
    schema: the protocol, and the key an enrolment is signed with. `None` when it is not one."""
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    return document if isinstance(document, dict) else None


def speaks(document: dict[str, Any] | None) -> None:
    """Refuse a daemon below the door's minimum, with the window (ADR 0148 §3): its `protocol` is read
    before the rest of its body is judged, so that a daemon too old for the body's present shape is
    told so and not that its body is invalid. A body that announces none is the schema's to refuse."""
    spoken = document.get("protocol") if document is not None else None
    if isinstance(spoken, int) and not isinstance(spoken, bool) and spoken < PROTOCOL_MINIMUM:
        raise Refused(
            OutpostDoorCode.PROTOCOL_UNSUPPORTED,
            f"this door serves protocol {PROTOCOL_MINIMUM} and above; the daemon speaks {spoken}",
        )


def shaped[Body: BaseModel](model: type[Body], raw: bytes) -> Body:
    """The body as the verb's schema describes it, or a 422 that says where it departs from it. The
    models are the contract's and are closed: a field the verb does not describe is refused, and so is
    a refusal code outside the list (ADR 0148 §9). Strict: a number is not read from a string."""
    try:
        return model.model_validate_json(raw, strict=True)
    except ValidationError as invalid:
        errors = [
            {"loc": [str(part) for part in error["loc"]], "msg": str(error["msg"])}
            for error in invalid.errors(include_url=False, include_context=False, include_input=False)
        ]
        raise Refused(
            OutpostDoorCode.INVALID_REQUEST,
            "the body is not what this verb's schema describes",
            errors=errors[:32],
        ) from None


# ───────────────────────── the signature ─────────────────────────

_INVALID: Final = "the request carries no signature this door takes"


def _invalid() -> Refused:
    """Absent, malformed, made by a key the door does not know, or not verifying: one answer."""
    return Refused(OutpostDoorCode.INVALID_SIGNATURE, _INVALID)


def _presented(request: Request) -> str:
    signature = request.headers.get(SIGNATURE_HEADER, "")
    if not signature:
        raise _invalid()
    return signature


def _protected_header(signature: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(signature)
    except jwt.InvalidTokenError:
        raise _invalid() from None
    return dict(header)


def _claims(signature: str, signing_key: str, *, session: bool) -> dict[str, Any]:
    """The claims of a signature that verifies under `signing_key` and is shaped as the contract says:
    the four every signature binds, and the nonce and the sequence of all but an enrolment's. A
    missing nonce is not judged here: it is the nonce's own refusal."""
    try:
        public = Ed25519PublicKey.from_public_bytes(_unb64(signing_key))
        verified = jwt.PyJWS().decode_complete(signature, key=public, algorithms=[SIGNATURE_ALGORITHM])
        claims = json.loads(verified["payload"])
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise _invalid() from None
    if not isinstance(claims, dict):
        raise _invalid()
    texts = (claims.get("htm"), claims.get("htu"), claims.get("body_sha256"))
    if not all(isinstance(value, str) for value in texts) or not _whole(claims.get("iat")):
        raise _invalid()
    if session and not (_whole(claims.get("seq")) and claims["seq"] >= 1):
        raise _invalid()
    return claims


def _whole(value: object) -> bool:
    """A whole number that fits 64 bits: what a moment and a sequence are, and what the row keeps."""
    return isinstance(value, int) and not isinstance(value, bool) and -MAX_SEQUENCE <= value <= MAX_SEQUENCE


def _url(claimed: str) -> str:
    """A signed URL as the door compares it: scheme and host in lower case, a default port left out,
    the rest as it is. A query, a fragment or a user stays where it was written, so a URL that
    carries one is not the door's."""
    scheme, separator, rest = claimed.partition("://")
    host, slash, path = rest.partition("/")
    if not separator or scheme.lower() not in {"https", "http"}:
        return ""
    scheme, host = scheme.lower(), host.lower()
    host = host.removesuffix({"https": ":443", "http": ":80"}[scheme])
    return f"{scheme}://{host}{slash}{path}"


def _same(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def check_binding(claims: dict[str, Any], *, method: str, url: str, body: bytes) -> None:
    """A signature that verifies and binds another method, another URL or another body is no signature
    of this request (401 `request_mismatch`)."""
    bound = (
        _same(claims["htm"], method.upper())
        and _same(_url(claims["htu"]), url)
        and _same(claims["body_sha256"], hashlib.sha256(body).hexdigest())
    )
    if not bound:
        raise Refused(
            OutpostDoorCode.REQUEST_MISMATCH,
            "the signature binds another method, another URL or another body than this request's",
        )


def check_iat(claims: dict[str, Any]) -> None:
    if abs(clock() - claims["iat"]) > IAT_WINDOW_S:
        raise Refused(
            OutpostDoorCode.STALE_IAT,
            f"the signature was issued more than {IAT_WINDOW_S} seconds from this door's clock",
        )


def _signed_request(request: Request, settings: Settings, body: bytes, claims: dict[str, Any]) -> None:
    """What every signature binds, then when it was made: checks 3 and 4."""
    url = f"{settings.outposts_door_origin}{request.scope['path']}"
    check_binding(claims, method=request.method, url=url, body=body)
    check_iat(claims)


def enrolment_signature(request: Request, settings: Settings, body: bytes, signing_key: str) -> None:
    """An enrolment is signed with the key it enrols: the enroller proves it holds that key, and the
    sealing key in the same body is tied to the signing key whose fingerprint a person compares
    (ADR 0148 §4). The same signature as any request's, without `kid`, nonce or sequence."""
    signature = _presented(request)
    if _protected_header(signature) != request_signature_header():
        raise _invalid()
    _signed_request(request, settings, body, _claims(signature, signing_key, session=False))


# ───────────────────────── who is speaking ─────────────────────────


@dataclass(frozen=True, slots=True)
class Caller:
    """One instance of one outpost of one organisation, as its signature resolved (ADR 0148 §6)."""

    outpost: Outpost
    #: The organisation's current slug.
    organisation: str
    instance: OutpostInstance
    principal: Principal


_limiter = RateLimiter(0)


def limited(key: str, settings: Settings) -> None:
    """One instance's requests in a minute, counted in memory and per replica like every limit of the
    API (`rate_limit.py`: there is no shared store, by decision). Beyond it: 429, and when to come back."""
    _limiter.per_minute = settings.outposts_door_rate_per_minute
    admitted, wait = _limiter.admits(key)
    if not admitted:
        raise Refused(
            OutpostDoorCode.RATE_LIMITED,
            f"more than {_limiter.per_minute} requests a minute from this instance",
            retry_after=wait,
        )


async def authenticate(request: Request, session: AsyncSession, settings: Settings, body: bytes) -> Caller:
    """Resolve a signed request to the instance that sent it: checks 2 to 8, in the contract's order.

    The session comes with no scope and leaves with one outpost's, inside its organisation's, both
    given by `scope_to_outpost` from the id the signature names. The sequence is taken once the
    earlier checks passed, by a compare-and-set on the instance's row, and committed at once: it is
    the row's from there on, whatever is refused next and at whichever replica a copy of the request
    arrives."""
    signature = _presented(request)
    header = _protected_header(signature)
    kid = header.get("kid")
    if not isinstance(kid, str) or header != request_signature_header(kid):
        raise _invalid()
    outpost_id = service.outpost_id_of_instance(kid) if re.fullmatch(OUTPOST_ID_PATTERN, kid) else None
    scope = await service.scope_to_outpost(session, outpost_id) if outpost_id else None
    if scope is None:
        raise _invalid()
    outpost, organisation = scope
    instance = (
        await session.execute(
            select(OutpostInstance).where(OutpostInstance.outpost_id == outpost.id, OutpostInstance.id == kid)
        )
    ).scalar_one_or_none()
    if instance is None:
        raise _invalid()
    claims = _claims(signature, instance.signing_key, session=True)
    _signed_request(request, settings, body, claims)
    if not nonces(settings).fresh(claims.get("nonce")):
        raise Refused(
            OutpostDoorCode.STALE_NONCE,
            "the nonce is absent or too old: sign again with the one this answer carries",
        )
    taken = await session.execute(
        update(OutpostInstance)
        .where(
            OutpostInstance.outpost_id == outpost.id,
            OutpostInstance.id == instance.id,
            OutpostInstance.sequence < claims["seq"],
        )
        .values(sequence=claims["seq"])
        .execution_options(synchronize_session=False)
    )
    if taken.rowcount != 1:  # type: ignore[attr-defined]
        raise Refused(
            OutpostDoorCode.REPLAYED_SEQUENCE,
            "the sequence is not above the last one this door took from this instance",
        )
    # From here on the sequence is the row's. The scope is carried to the session's next transaction.
    await release_connection(session)
    if outpost.status == OutpostStatus.REVOKED.value:
        raise Refused(OutpostDoorCode.INSTANCE_REVOKED, "this instance's outpost was revoked")
    if instance.status != OutpostInstanceStatus.ACTIVE.value:
        code, detail = _SESSION_REFUSALS.get(
            instance.status, _SESSION_REFUSALS[OutpostInstanceStatus.REVOKED.value]
        )
        raise Refused(code, detail)
    limited(instance.id, settings)
    return Caller(
        outpost=outpost,
        organisation=organisation,
        instance=instance,
        principal=outpost_principal(outpost_id=outpost.id, instance_id=instance.id),
    )


def check_the_door(settings: Settings) -> None:
    """A door that is switched on has what it cannot work without: its public origin, which a
    signature's URL is compared with, and its signing key, without which no outpost would make a call
    it hands over. Refused at start-up, naming the settings, rather than found one refusal at a time
    by whoever runs an outpost."""
    missing = [
        name
        for name, value in (
            ("CHOREGOS_OUTPOSTS_DOOR_URL", settings.outposts_door_url),
            ("CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY", settings.outposts_door_signing_key),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"CHOREGOS_OUTPOSTS is on and {' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} "
            "not set: the outposts' door cannot verify a request's URL without its public origin, nor "
            "sign a call without its key. Set them, or switch outposts off."
        )


__all__ = [
    "MAX_SEQUENCE",
    "NONCE_STATUSES",
    "PROTOCOL_CURRENT",
    "PROTOCOL_MINIMUM",
    "Caller",
    "Nonces",
    "Refused",
    "answer",
    "authenticate",
    "check_binding",
    "check_iat",
    "check_the_door",
    "clock",
    "document_of",
    "enrolment_signature",
    "limited",
    "nonces",
    "problem",
    "read_body",
    "server_time",
    "shaped",
    "speaks",
    "window",
]
