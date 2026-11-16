# SPDX-License-Identifier: Apache-2.0
"""A scriptable fake of the outposts' door (ADR 0005), built from the contract and not from guesses.

The real door is the API's (`/outposts/v1`, ADR 0148). This one stands in for it wherever a daemon
is tested without a platform: it is one outpost of one organisation, in memory. What it does, it
reads from `outpost-door.yaml`:

- the four verbs and their paths, each body's cap (`x-max-body-bytes`) and schema;
- the order of the checks (`x-request-signature.checks`): the body's size, the signature, what it
  binds, `iat`, the nonce, the sequence (taken from then on), the instance, the rate, the protocol,
  the body's schema, then what the verb itself refuses;
- every problem with its `code`, its status (`DOOR_STATUS`) and `server_time`;
- a call handed over is sealed to the claiming instance (`choregos_adapters.sealing`) and signed by
  the door (`x-call-signature`), under a key derived from a label as the contract's vectors are:
  test material, never a key in service;
- one poll per instance at a time; a lease; an answer taken from the claiming instance only, under
  the epoch of the claim, before the deadline, once.

It is reached without a socket (`transport()`, an `httpx` transport) or served by any ASGI server
(the object is a bare ASGI application). A test scripts it: `enrolment_token`, `approve`, `revoke`,
`pause`, `serve` (the protocol window), `submit` (a call to hand over), `hand_as_is` (anything at
all in a poll's answer: a forged call, an unsigned one), `cancel`, `refuse_next`, `skew`. And reads
it: `answers`, `refusals`, `received`, `alerts`.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import jwt
from choregos_adapters import sealing
from choregos_contracts import load_outpost_door, load_schema
from choregos_contracts.enums import OutpostCallKind, OutpostDoorCode, OutpostInstanceStatus
from choregos_contracts.outpost import (
    DOOR_STATUS,
    NONCE_HEADER,
    OUTPOST_DOOR_PREFIX,
    REQUEST_SIGNATURE_TYPE,
    SIGNATURE_ALGORITHM,
    SIGNATURE_HEADER,
    OutpostAnswer,
    call_signature_claims,
    call_signature_header,
    fingerprint_of,
    request_signature_claims,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from jsonschema import Draft202012Validator

_RAW = (serialization.Encoding.Raw, serialization.PublicFormat.Raw)
_CALL_PATH = re.compile(
    rf"^{OUTPOST_DOOR_PREFIX}/calls/(?P<id>[A-Za-z0-9][A-Za-z0-9_-]{{0,127}})/(?P<verb>answer|refuse)$"
)
#: The label the fake door's signing key is derived from, as the contract's vectors derive theirs.
DOOR_KEY_LABEL = "choregos-outpost fake door: signing key"
_TITLES = {401: "Not authenticated", 403: "Forbidden", 404: "Not found", 409: "Conflict"}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _seed(label: str) -> bytes:
    """Private test material: the SHA-256 of a label, so that no key is written anywhere."""
    return hashlib.sha256(label.encode("utf-8")).digest()


def _stamp(moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class FakeInstance:
    id: str
    signing_key: str
    sealing_key: str
    status: OutpostInstanceStatus = OutpostInstanceStatus.PENDING
    #: The last sequence the door took from this instance: a request not above it is a replay.
    sequence: int = 0
    polling: bool = False
    facts: dict[str, str] = field(default_factory=dict)
    daemon_version: str = ""
    endpoints: list[str] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        return fingerprint_of(self.signing_key)


@dataclass
class FakeCall:
    """A call the platform routed to the outpost: pending, then claimed, then answered or refused."""

    id: str
    plaintext: dict[str, Any]
    lease_s: float
    answer_private: X25519PrivateKey
    state: str = "pending"
    claimed_by: str | None = None
    epoch: int | None = None
    deadline: float | None = None
    #: How many times it was handed over.
    handed: int = 0
    recorded: str | None = None


class _ProblemError(Exception):
    def __init__(self, code: OutpostDoorCode, detail: str = "", **extra: Any) -> None:
        self.code = code
        self.detail = detail
        self.extra = extra
        super().__init__(code.value)


class FakeDoor:
    def __init__(
        self,
        *,
        origin: str = "https://outposts.example.com",
        organisation: str = "acme",
        outpost: str = "factory-floor",
        now: Callable[[], float] = time.time,
        max_wait_s: float = 0.05,
        key_label: str = DOOR_KEY_LABEL,
    ) -> None:
        self.origin = origin.rstrip("/")
        self.organisation = organisation
        self.outpost = outpost
        self._now = now
        #: A poll waits this long at most, whatever the daemon asks: a test does not wait 25 seconds.
        self.max_wait_s = max_wait_s
        self.contract = load_outpost_door()
        self._bounds = self.contract["x-bounds"]
        self._key = Ed25519PrivateKey.from_private_bytes(_seed(key_label))
        self._nonce_secret = _seed(key_label + ": nonce")
        self.current, self.minimum = 1, 1
        self.paused = False
        self.revoked = False
        self.epoch = 1
        self.clock_skew_s = 0.0
        self.tokens: dict[str, float] = {}
        self.instances: dict[str, FakeInstance] = {}
        self.calls: dict[str, FakeCall] = {}
        self.answers: dict[str, OutpostAnswer] = {}
        self.refusals: dict[str, str] = {}
        #: Every request received: (verb, the claims of its signature or None, its body as text).
        self.received: list[tuple[str, dict[str, Any] | None, str]] = []
        #: What a real door would alert on: a second poll under one key.
        self.alerts: list[str] = []
        self._as_is: list[dict[str, Any]] = []
        self._cancelled: set[str] = set()
        self._scripted: dict[str, list[_ProblemError]] = {}

    # ───────────────────────── what a test scripts ─────────────────────────

    @property
    def public_key(self) -> str:
        """The key to pin in a policy file's `door_keys`."""
        return _b64(self._key.public_key().public_bytes(*_RAW))

    def enrolment_token(self, ttl_s: float | None = None) -> str:
        """A token for one instance: single use, valid 15 minutes at most."""
        token = secrets.token_urlsafe(24)
        self.tokens[token] = self.now() + min(
            ttl_s or self._bounds["enrolment_ttl_s"], self._bounds["enrolment_ttl_s"]
        )
        return token

    def approve(self, instance_id: str, fingerprint: str | None = None) -> None:
        """What a person does in the console: the fingerprint they compare must be the instance's."""
        instance = self.instances[instance_id]
        if fingerprint is not None and fingerprint != instance.fingerprint:
            raise ValueError("the fingerprint is not this instance's: nothing is approved")
        instance.status = OutpostInstanceStatus.ACTIVE

    def revoke(self, instance_id: str) -> None:
        """Revoking an instance moves the outpost's epoch: nothing in flight is taken (ADR 0147 §5)."""
        self.instances[instance_id].status = OutpostInstanceStatus.REVOKED
        self.epoch += 1

    def pause(self, paused: bool = True) -> None:
        self.paused = paused

    def serve(self, *, current: int, minimum: int) -> None:
        """The protocol window the door announces; a daemon below `minimum` gets a 426."""
        self.current, self.minimum = current, minimum

    def skew(self, seconds: float) -> None:
        """Moves the door's clock from the machine's: positive, the door is ahead."""
        self.clock_skew_s = seconds

    def now(self) -> float:
        return self._now() + self.clock_skew_s

    def submit(
        self,
        endpoint: str,
        tool: str | None = None,
        arguments: dict[str, Any] | None = None,
        *,
        kind: str | None = None,
        call_id: str | None = None,
        lease_s: float = 60.0,
        asserted: dict[str, Any] | None = None,
        **changed: Any,
    ) -> FakeCall:
        """Routes a call to the outpost: it is handed to the next instance that polls with room.
        `changed` overrides fields of the plaintext, to build a call the contract does not describe."""
        call_id = call_id or str(uuid.uuid4())
        kind = kind or (
            OutpostCallKind.CALL_TOOL.value if tool is not None else OutpostCallKind.LIST_TOOLS.value
        )
        answer_private = X25519PrivateKey.generate()
        plaintext: dict[str, Any] = {"id": call_id, "kind": kind, "endpoint": endpoint}
        if kind == OutpostCallKind.CALL_TOOL.value:
            plaintext |= {"tool": tool, "arguments": arguments or {}}
        plaintext |= {
            "answer_key": _b64(answer_private.public_key().public_bytes(*_RAW)),
            "asserted": asserted or {"org": self.organisation, "purpose": "tool_call"},
        }
        plaintext |= changed
        call = FakeCall(call_id, plaintext, min(lease_s, self._bounds["max_lease_s"]), answer_private)
        self.calls[call_id] = call
        return call

    def sealed_for(self, instance_id: str, call_id: str, plaintext: dict[str, Any]) -> str:
        """An envelope only this instance opens."""
        return sealing.seal(self.instances[instance_id].sealing_key, call_id, plaintext)

    def signature_for(
        self,
        call_id: str,
        instance_id: str,
        sealed: str,
        *,
        signed_with: Ed25519PrivateKey | None = None,
        kid: str | None = None,
        header: dict[str, str] | None = None,
        **more_claims: Any,
    ) -> str:
        """A call's signature as the contract describes it, or, with the keywords, one it refuses."""
        signer = signed_with or self._key
        named = kid or _b64(signer.public_key().public_bytes(*_RAW))
        claims = call_signature_claims(call_id=call_id, instance_id=instance_id, sealed=sealed) | more_claims
        return jwt.encode(
            claims,
            signer,
            algorithm=SIGNATURE_ALGORITHM,
            headers=call_signature_header(named) | (header or {}),
        )

    def hand_as_is(self, document: dict[str, Any]) -> None:
        """Puts `document` among the calls of the next poll's answer, untouched: what whoever sits on
        the path could add (a forged call, an unsigned one, a replayed one)."""
        self._as_is.append(document)

    def cancel(self, call_id: str) -> None:
        """The door waits for this call no more: the next poll that names it running is told."""
        self._cancelled.add(call_id)
        if call_id in self.calls:
            self.calls[call_id].state = "cancelled"

    def refuse_next(self, verb: str, code: OutpostDoorCode, *, times: int = 1, **extra: Any) -> None:
        """The next `times` requests of `verb` (`poll`, `answer`, `refuse`, `enrol`) that pass the
        signature are answered this problem."""
        self._scripted.setdefault(verb, []).extend(_ProblemError(code, **extra) for _ in range(times))

    async def answered(self, call_id: str, timeout_s: float = 5.0) -> OutpostAnswer:
        await self._until(lambda: call_id in self.answers, timeout_s, f"no answer to {call_id}")
        return self.answers[call_id]

    async def refused(self, call_id: str, timeout_s: float = 5.0) -> str:
        await self._until(lambda: call_id in self.refusals, timeout_s, f"no refusal of {call_id}")
        return self.refusals[call_id]

    async def polled(self, times: int = 1, timeout_s: float = 5.0) -> None:
        start = self.polls
        await self._until(lambda: self.polls >= start + times, timeout_s, "the daemon did not poll")

    @property
    def polls(self) -> int:
        return sum(1 for verb, _, _ in self.received if verb == "poll")

    @staticmethod
    async def _until(reached: Callable[[], bool], timeout_s: float, otherwise: str) -> None:
        deadline = time.monotonic() + timeout_s
        while not reached():
            if time.monotonic() > deadline:
                raise AssertionError(otherwise)
            await asyncio.sleep(0.005)

    # ───────────────────────── how it is reached ─────────────────────────

    def transport(self) -> httpx.MockTransport:
        """An `httpx` transport that reaches this door without a socket."""
        return httpx.MockTransport(self.handle)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """A bare ASGI application: any ASGI server serves the door over real HTTP."""
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                await send({"type": message["type"] + ".complete"})
                if message["type"] == "lifespan.shutdown":
                    return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        headers = [(name.decode("latin-1"), value.decode("latin-1")) for name, value in scope["headers"]]
        response = await self.handle(
            httpx.Request(scope["method"], self.origin + scope["path"], headers=headers, content=body)
        )
        sent = [(name.encode("latin-1"), value.encode("latin-1")) for name, value in response.headers.items()]
        await send({"type": "http.response.start", "status": response.status_code, "headers": sent})
        await send({"type": "http.response.body", "body": response.content})

    async def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        try:
            if request.method != "POST":
                return httpx.Response(405)
            if path == f"{OUTPOST_DOOR_PREFIX}/enrol":
                return await self._enrol(request)
            if path == f"{OUTPOST_DOOR_PREFIX}/poll":
                return await self._poll(request)
            matched = _CALL_PATH.match(path)
            if matched:
                return await self._record(request, matched["id"], matched["verb"])
        except _ProblemError as problem:
            return self._problem(problem, path)
        # Not one of the door's paths: a 404 that is not one of this contract's problems.
        return httpx.Response(404, json={"detail": "Not Found"})

    # ───────────────────────── the checks, in the contract's order ─────────────────────────

    def _operation(self, verb: str) -> dict[str, Any]:
        path = {"enrol": "/enrol", "poll": "/poll"}.get(verb, f"/calls/{{id}}/{verb}")
        operation: dict[str, Any] = self.contract["paths"][OUTPOST_DOOR_PREFIX + path]["post"]
        return operation

    def _valid(self, verb: str, document: Any) -> list[str]:
        schema = self._operation(verb)["requestBody"]["content"]["application/json"]["schema"]
        reference = schema.get("$ref", "")
        if reference.startswith("./schemas/"):
            resolved = load_schema(reference.removeprefix("./schemas/"))
        else:
            resolved = {"components": self.contract["components"], **schema}
        validator = Draft202012Validator(resolved, format_checker=Draft202012Validator.FORMAT_CHECKER)
        return [f"{list(error.absolute_path)}: {error.message}" for error in validator.iter_errors(document)]

    def _nonce(self) -> str:
        issued = str(int(self.now()))
        mac = hmac.new(self._nonce_secret, issued.encode(), hashlib.sha256).digest()
        return f"v1.{issued}.{_b64(mac)[:22]}"

    def _fresh(self, nonce: Any) -> bool:
        """A nonce this door issued, less than `nonce_ttl_s` ago: checked alone, nothing stored."""
        if not isinstance(nonce, str) or nonce.count(".") != 2:
            return False
        _, issued, mac = nonce.split(".")
        expected = _b64(hmac.new(self._nonce_secret, issued.encode(), hashlib.sha256).digest())[:22]
        if not hmac.compare_digest(mac, expected) or not issued.isdigit():
            return False
        return bool(0 <= self.now() - int(issued) <= self._bounds["nonce_ttl_s"])

    def _verified(self, request: httpx.Request, verb: str, public: Ed25519PublicKey) -> dict[str, Any]:
        """The claims of a request's signature, once it verifies and binds this very request."""
        try:
            claims: dict[str, Any] = jwt.decode(
                request.headers.get(SIGNATURE_HEADER, ""),
                public,
                algorithms=[SIGNATURE_ALGORITHM],
                options={"verify_iat": False, "verify_exp": False},
            )
        except jwt.InvalidTokenError as error:
            raise _ProblemError(OutpostDoorCode.INVALID_SIGNATURE, "the signature does not verify") from error
        url = self.origin + request.url.path
        bound = request_signature_claims(method="POST", url=url, body=request.content, iat=0)
        if any(claims.get(name) != bound[name] for name in ("htm", "htu", "body_sha256")):
            raise _ProblemError(OutpostDoorCode.REQUEST_MISMATCH, "the signature binds another request")
        iat = claims.get("iat")
        if not isinstance(iat, int) or abs(self.now() - iat) > self._bounds["iat_window_s"]:
            raise _ProblemError(
                OutpostDoorCode.STALE_IAT, "`iat` is more than 60 seconds from this door's clock"
            )
        return claims

    def _session(self, request: httpx.Request, verb: str) -> tuple[FakeInstance, Any]:
        """The instance a signed request is from, and its body: every check up to the verb's own."""
        if len(request.content) > self._operation(verb)["x-max-body-bytes"]:
            raise _ProblemError(OutpostDoorCode.BODY_TOO_LARGE)
        try:
            header = jwt.get_unverified_header(request.headers.get(SIGNATURE_HEADER, ""))
        except jwt.InvalidTokenError as error:
            self.received.append((verb, None, request.content.decode("utf-8", "replace")))
            raise _ProblemError(
                OutpostDoorCode.INVALID_SIGNATURE, "no signature this door can read"
            ) from error
        instance = self.instances.get(str(header.get("kid")))
        if instance is None or header.get("typ") != REQUEST_SIGNATURE_TYPE:
            self.received.append((verb, None, request.content.decode("utf-8", "replace")))
            raise _ProblemError(OutpostDoorCode.INVALID_SIGNATURE, "a key this door does not know")
        public = Ed25519PublicKey.from_public_bytes(_unb64(instance.signing_key))
        try:
            claims = self._verified(request, verb, public)
        except _ProblemError:
            self.received.append((verb, None, request.content.decode("utf-8", "replace")))
            raise
        self.received.append((verb, claims, request.content.decode("utf-8", "replace")))
        if not self._fresh(claims.get("nonce")):
            raise _ProblemError(OutpostDoorCode.STALE_NONCE, "sign again with the nonce this answer carries")
        sequence = claims.get("seq")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= instance.sequence:
            raise _ProblemError(
                OutpostDoorCode.REPLAYED_SEQUENCE, "the sequence is not above the last one taken"
            )
        instance.sequence = sequence
        if self.revoked or instance.status is OutpostInstanceStatus.REVOKED:
            raise _ProblemError(OutpostDoorCode.INSTANCE_REVOKED)
        if instance.status is OutpostInstanceStatus.PENDING:
            raise _ProblemError(OutpostDoorCode.INSTANCE_PENDING, "nobody approved this instance yet")
        if self._scripted.get(verb):
            raise self._scripted[verb].pop(0)
        try:
            document = json.loads(request.content)
        except ValueError as error:
            raise _ProblemError(OutpostDoorCode.INVALID_REQUEST, "the body is not JSON") from error
        self._protocol(document)
        faults = self._valid(verb, document)
        if faults:
            raise _ProblemError(OutpostDoorCode.INVALID_REQUEST, "; ".join(faults))
        return instance, document

    def _protocol(self, document: Any) -> None:
        """Read before the rest of the body is judged: a daemon too old for it is told so."""
        spoken = document.get("protocol") if isinstance(document, dict) else None
        if isinstance(spoken, int) and spoken < self.minimum:
            raise _ProblemError(
                OutpostDoorCode.PROTOCOL_UNSUPPORTED,
                f"this door serves protocol {self.minimum} and above; the daemon speaks {spoken}",
                protocol=self._window(),
            )

    def _window(self) -> dict[str, int]:
        return {"current": self.current, "minimum": self.minimum}

    def _problem(self, problem: _ProblemError, path: str) -> httpx.Response:
        status = DOOR_STATUS[problem.code]
        body: dict[str, Any] = {
            "type": "about:blank",
            "title": _TITLES.get(status, "Refused"),
            "status": status,
            "code": problem.code.value,
            "instance": path,
            "server_time": _stamp(self.now()),
            **problem.extra,
        }
        if problem.detail:
            body["detail"] = problem.detail
        headers = {"Content-Type": "application/problem+json"}
        if status in (401, 403, 404, 409) and not path.endswith("/enrol"):
            headers[NONCE_HEADER] = self._nonce()
        if status == 426:
            headers["Upgrade"] = f"choregos-outpost/{self.minimum}"
        return httpx.Response(status, content=json.dumps(body).encode(), headers=headers)

    def _answer(self, status: int, document: dict[str, Any]) -> httpx.Response:
        return httpx.Response(status, json=document, headers={NONCE_HEADER: self._nonce()})

    # ───────────────────────── the four verbs ─────────────────────────

    async def _enrol(self, request: httpx.Request) -> httpx.Response:
        if len(request.content) > self._operation("enrol")["x-max-body-bytes"]:
            raise _ProblemError(OutpostDoorCode.BODY_TOO_LARGE)
        try:
            document = json.loads(request.content)
            key = document["signing_key"]
            public = Ed25519PublicKey.from_public_bytes(_unb64(key))
        except (ValueError, KeyError, TypeError) as error:
            # A body in which no signing key can be read is a 422 before the signature is looked at.
            raise _ProblemError(OutpostDoorCode.INVALID_REQUEST, "no `signing_key` can be read") from error
        claims = self._verified(request, "enrol", public)
        self.received.append(("enrol", claims, request.content.decode("utf-8", "replace")))
        token = document.get("token")
        known = [instance for instance in self.instances.values() if instance.signing_key == key]
        if not isinstance(token, str) or self.tokens.get(token, 0) < self.now():
            raise _ProblemError(OutpostDoorCode.INVALID_ENROLMENT_TOKEN)
        if known and known[0].status is not OutpostInstanceStatus.PENDING:
            raise _ProblemError(OutpostDoorCode.KEY_ALREADY_ENROLLED)
        if self._scripted.get("enrol"):
            raise self._scripted["enrol"].pop(0)
        self._protocol(document)
        faults = self._valid("enrol", document)
        if faults:
            raise _ProblemError(OutpostDoorCode.INVALID_REQUEST, "; ".join(faults))
        del self.tokens[token]
        # A key still pending at this outpost is the same instance again, under the new token.
        instance = known[0] if known else FakeInstance(str(uuid.uuid4()), key, document["sealing_key"])
        instance.sealing_key = document["sealing_key"]
        instance.facts = dict(document.get("facts") or {})
        instance.daemon_version = document["daemon_version"]
        self.instances[instance.id] = instance
        return self._answer(
            201,
            {
                "instance_id": instance.id,
                "organisation": self.organisation,
                "outpost": self.outpost,
                "status": "pending",
                "fingerprint": instance.fingerprint,
                "protocol": self._window(),
                "server_time": _stamp(self.now()),
            },
        )

    def _hand(self, instance: FakeInstance, capacity: int) -> list[dict[str, Any]]:
        """Claims up to `capacity` calls for this instance: each sealed to it and signed by the door."""
        handed: list[dict[str, Any]] = []
        now = self.now()
        for call in self.calls.values():
            if len(handed) >= capacity or self.paused:
                break
            if call.state != "pending":
                continue
            call.state, call.claimed_by, call.epoch = "claimed", instance.id, self.epoch
            call.deadline = now + call.lease_s
            call.handed += 1
            plaintext = dict(call.plaintext)
            plaintext.setdefault("deadline", _stamp(call.deadline))
            sealed = sealing.seal(instance.sealing_key, call.id, plaintext)
            signature = self.signature_for(call.id, instance.id, sealed)
            handed.append({"id": call.id, "sealed": sealed, "signature": signature})
        return handed

    async def _poll(self, request: httpx.Request) -> httpx.Response:
        instance, document = self._session(request, "poll")
        if instance.polling:
            self.alerts.append(f"poll_conflict: a second poll under the key of instance {instance.id}")
            raise _ProblemError(
                OutpostDoorCode.POLL_CONFLICT, "another poll is waiting under this key", retry_after=1
            )
        instance.polling = True
        instance.endpoints = list(document.get("endpoints", []))
        try:
            until = time.monotonic() + min(float(document.get("wait_s", 25)), self.max_wait_s)
            while True:
                calls = self._hand(instance, int(document["capacity"]))
                extra, self._as_is = self._as_is, []
                cancelled = sorted(self._cancelled & set(document.get("running", [])))
                if calls or extra or cancelled or time.monotonic() >= until:
                    break
                await asyncio.sleep(0.005)
        finally:
            instance.polling = False
        self._cancelled -= set(cancelled)
        return self._answer(
            200,
            {
                "protocol": self._window(),
                "server_time": _stamp(self.now()),
                "stop": self.paused,
                "calls": [*calls, *extra],
                "cancel": cancelled,
            },
        )

    async def _record(self, request: httpx.Request, call_id: str, verb: str) -> httpx.Response:
        instance, document = self._session(request, verb)
        call = self.calls.get(call_id)
        if call is None or call.claimed_by is None:
            # A call that is not this outpost's answers the same as one that does not exist.
            raise _ProblemError(OutpostDoorCode.NOT_FOUND)
        if call.recorded is None:
            lost = call.claimed_by != instance.id or call.epoch != self.epoch or call.state == "cancelled"
            if lost or (call.deadline is not None and self.now() > call.deadline):
                raise _ProblemError(
                    OutpostDoorCode.LEASE_LOST, "the call is not leased to this instance any more"
                )
            call.recorded = "answer" if verb == "answer" else "refusal"
            call.state = "answered" if verb == "answer" else "refused"
            if verb == "answer":
                opened = sealing.open_sealed(call.answer_private, call_id, document["sealed"])
                self.answers[call_id] = OutpostAnswer.model_validate(opened)
            else:
                self.refusals[call_id] = document["code"]
        elif call.claimed_by != instance.id:
            raise _ProblemError(OutpostDoorCode.LEASE_LOST)
        # Recorded once: sending again reads what was recorded, whatever is sent.
        return self._answer(200, {"id": call_id, "recorded": call.recorded})
