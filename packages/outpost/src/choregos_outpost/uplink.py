# SPDX-License-Identifier: Apache-2.0
"""The uplink: the door's four verbs, as their client (ADR 0148 §3, §4; `outpost-door.yaml`).

Every connection the outpost opens towards the platform goes to one origin, the policy file's
`control_plane`, and is opened here. The door never calls back.

A request is signed exactly as `x-request-signature` says: a compact JWS made with the instance's
Ed25519 key, which binds the method, the URL (`control_plane` followed by the verb's path), the
SHA-256 of the body as sent, `iat`, the last nonce the door issued and a sequence number the
instance raises with every request it signs.

- **The nonce.** The daemon keeps the last `Outpost-Nonce` it received. Its first signed request
  has none: it is refused `stale_nonce`, and that refusal carries a nonce to sign again with.
- **The sequence.** Raised with every signature, never reused: a request the door refused before
  it took the sequence is signed again under a higher one, and so is the lower of two requests
  that crossed (`replayed_sequence`). It is the later of "one more than the last" and the clock in
  milliseconds, and the last value is kept on disk, so that neither a restart nor a clock set back
  makes the daemon reuse a number.
- **The enrolment** is signed with the key it enrols, without `kid`, nonce or sequence: none
  exists yet.

Nothing here follows a redirect (the signature binds the URL), and no answer is read without a
bound. What the door refuses comes back as `DoorRefusedError`, with the door's problem and its
`server_time`; what never reached the door, or answered something that is not the door's, as
`DoorUnreachableError`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from choregos_contracts.enums import OutpostDoorCode, OutpostRefusalCode
from choregos_contracts.outpost import (
    MAX_WAIT_S,
    NONCE_HEADER,
    OUTPOST_DOOR_PREFIX,
    SIGNATURE_HEADER,
    OutpostCallAck,
    OutpostDoorProblem,
    OutpostEnrolmentAnswer,
    OutpostEnrolmentRequest,
    OutpostPollAnswer,
    OutpostPollRequest,
    OutpostRefusal,
    OutpostSealedAnswer,
    request_signature_claims,
    request_signature_header,
)
from pydantic import ValidationError

from . import PROTOCOL, __version__
from .identity import Identity

#: A refusal that says "sign again": the door took no sequence, or took a higher one first.
_SIGN_AGAIN = (OutpostDoorCode.STALE_NONCE, OutpostDoorCode.REPLAYED_SEQUENCE)
#: How many times one request is signed before its refusal is handed to the caller.
_SIGNATURES = 3
#: The most the daemon reads of an answer that is not a poll's: a recorded call, a problem.
_SMALL_ANSWER = 1 << 20
#: What a sealed call may weigh in a poll's answer: the largest arguments a policy file allows
#: (1 MiB), sealed and encoded, with room to spare.
_PER_CALL = 3 << 20
CAPABILITIES: list[Any] = ["tools"]


class DoorError(Exception):
    """The door did not take a request."""


class DoorUnreachableError(DoorError):
    """No answer of the door: the network, a proxy on the path, or an origin that is not the door."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        self.status = status
        super().__init__(message)


class DoorRefusedError(DoorError):
    """The door answered one of its problems (RFC 9457, with a `code` and the door's time)."""

    def __init__(self, problem: OutpostDoorProblem) -> None:
        self.problem = problem
        super().__init__(f"{problem.status} {problem.code}: {problem.detail or problem.title}")

    @property
    def code(self) -> OutpostDoorCode | None:
        """The code, when protocol 1 knows it; a daemon goes by the HTTP status otherwise."""
        return self.problem.known_code

    @property
    def status(self) -> int:
        return self.problem.status


class Sequence:
    """The instance's sequence: a whole number it raises with every request it signs.

    The later of "one more than the last" and the clock in milliseconds, so that it survives a
    restart without the file and a clock set back with it. The file is a floor, not a record the
    daemon's safety rests on: if the disk refuses it, the daemon says so once and goes on."""

    def __init__(self, path: Path | None = None, *, now: Callable[[], float] = time.time) -> None:
        self._path = path
        self._now = now
        self._last = self._read()
        self.unwritable: str | None = None

    def _read(self) -> int:
        if self._path is None:
            return 0
        try:
            return max(0, int(self._path.read_text(encoding="ascii").strip() or 0))
        except (OSError, ValueError):
            return 0

    def _write(self, value: int) -> None:
        assert self._path is not None
        temporary = self._path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as file:
            file.write(str(value))
        temporary.replace(self._path)

    async def next(self) -> int:
        self._last = max(self._last + 1, int(self._now() * 1000))
        if self._path is not None:
            try:
                await asyncio.to_thread(self._write, self._last)
                self.unwritable = None
            except OSError as error:
                self.unwritable = error.strerror or str(error)
        return self._last


class Backoff:
    """How long to wait before asking again: twice as long each time up to `cap`, and never the same
    wait twice. The jitter is half the wait: a fleet of daemons the door dropped at once does not
    come back at once."""

    def __init__(self, base: float = 1.0, cap: float = 60.0, rng: random.Random | None = None) -> None:
        self._base = base
        self._cap = cap
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not a secret
        self.attempt = 0

    def next(self) -> float:
        ceiling = min(self._cap, self._base * 2 ** min(self.attempt, 32))
        self.attempt += 1
        return self._rng.uniform(ceiling / 2, ceiling)

    def reset(self) -> None:
        self.attempt = 0


def client(
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    verify: Any = True,
    proxy: str | None = None,
) -> httpx.AsyncClient:
    """The HTTP client of the uplink: no redirect, no ambient configuration, one optional proxy."""
    timeout = httpx.Timeout(connect=10.0, read=MAX_WAIT_S + 10.0, write=30.0, pool=10.0)
    if transport is not None:
        return httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False)
    return httpx.AsyncClient(
        verify=verify, proxy=proxy, trust_env=False, timeout=timeout, follow_redirects=False
    )


class Uplink:
    def __init__(
        self,
        control_plane: str,
        identity: Identity,
        http: httpx.AsyncClient,
        *,
        instance_id: str | None = None,
        sequence: Sequence | None = None,
        now: Callable[[], float] = time.time,
        daemon_version: str = __version__,
    ) -> None:
        self.control_plane = control_plane.rstrip("/")
        self.instance_id = instance_id
        self._identity = identity
        self._http = http
        self._sequence = sequence or Sequence()
        self._now = now
        self._version = daemon_version
        self._nonce: str | None = None
        #: The door's clock as its last answer or problem gave it, with the moment it was read.
        self.server_time: tuple[float, float] | None = None
        #: Requests other than the poll go one at a time: two answers sent at once would cross.
        self._one_at_a_time = asyncio.Lock()

    @property
    def sequence_fault(self) -> str | None:
        """Why the last sequence number could not be kept on disk, if it could not."""
        return self._sequence.unwritable

    def url(self, path: str) -> str:
        return f"{self.control_plane}{OUTPOST_DOOR_PREFIX}{path}"

    async def aclose(self) -> None:
        await self._http.aclose()

    # ───────────────────────── one request ─────────────────────────

    async def _signature(self, url: str, body: bytes, *, session: bool) -> str:
        """The value of `Outpost-Signature` for this request, under a sequence never used before."""
        claims = request_signature_claims(
            method="POST",
            url=url,
            body=body,
            iat=int(self._now()),
            nonce=self._nonce if session else None,
            seq=await self._sequence.next() if session else None,
        )
        return self._identity.sign(claims, request_signature_header(self.instance_id if session else None))

    async def _post(
        self, url: str, body: bytes, signature: str, limit: int
    ) -> tuple[int, httpx.Headers, bytes]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, application/problem+json",
            "User-Agent": f"choregos-outpost/{self._version}",
            SIGNATURE_HEADER: signature,
        }
        try:
            async with self._http.stream("POST", url, content=body, headers=headers) as response:
                read = bytearray()
                async for chunk in response.aiter_bytes():
                    read += chunk
                    if len(read) > limit:
                        raise DoorUnreachableError(
                            f"an answer above {limit} bytes is not the door's: not read"
                        )
                return response.status_code, response.headers, bytes(read)
        except httpx.HTTPError as error:
            raise DoorUnreachableError(f"{type(error).__name__}: {error}".rstrip(": ")) from error

    def _learn(self, headers: httpx.Headers, document: Any) -> None:
        """Keeps the nonce to sign the next request with, and the door's clock."""
        nonce = headers.get(NONCE_HEADER)
        if nonce:
            self._nonce = nonce
        stamp = document.get("server_time") if isinstance(document, dict) else None
        if isinstance(stamp, str):
            with contextlib.suppress(ValueError):
                self.server_time = (datetime.fromisoformat(stamp).timestamp(), self._now())

    async def _send(self, path: str, body: bytes, *, session: bool = True, limit: int = _SMALL_ANSWER) -> Any:
        """Sends one request, signing it again when the door asks for a fresh signature. Returns the
        answer's document; raises `DoorRefusedError` or `DoorUnreachableError`."""
        url = self.url(path)
        for signature_number in range(1, _SIGNATURES + 1):
            status, headers, raw = await self._post(
                url, body, await self._signature(url, body, session=session), limit
            )
            try:
                document = json.loads(raw) if raw else None
            except ValueError:
                document = None
            self._learn(headers, document)
            if 200 <= status < 300:
                if not isinstance(document, dict):
                    raise DoorUnreachableError(
                        f"HTTP {status} whose body is not a JSON object", status=status
                    )
                return document
            problem = self._problem(status, document)
            again = session and problem.known_code in _SIGN_AGAIN and signature_number < _SIGNATURES
            if not again:
                raise DoorRefusedError(problem)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _problem(status: int, document: Any) -> OutpostDoorProblem:
        try:
            return OutpostDoorProblem.model_validate(document)
        except ValidationError:
            pass
        if status == 404:
            raise DoorUnreachableError(
                "404 that is not one of the door's problems: outposts are switched off at this origin, "
                "or `control_plane` is not the door's",
                status=status,
            )
        raise DoorUnreachableError(f"HTTP {status} that is not one of the door's problems", status=status)

    # ───────────────────────── the four verbs ─────────────────────────

    async def enrol(self, token: str, facts: dict[str, str]) -> OutpostEnrolmentAnswer:
        """Presents an enrolment token with the instance's public keys, signed with the key it enrols.
        The instance is born pending: a person approves it against its fingerprint."""
        request = OutpostEnrolmentRequest(
            protocol=PROTOCOL,
            token=token,
            signing_key=self._identity.signing_key,
            sealing_key=self._identity.sealing_key,
            daemon_version=self._version,
            capabilities=CAPABILITIES,
            facts=facts,
        )
        document = await self._send("/enrol", request.model_dump_json().encode("utf-8"), session=False)
        return self._read(OutpostEnrolmentAnswer, document)

    async def poll(
        self, *, wait_s: int, capacity: int, endpoints: list[str], running: list[str]
    ) -> OutpostPollAnswer:
        """Waits for calls to make. One poll at a time: the caller awaits this one before the next."""
        request = OutpostPollRequest(
            protocol=PROTOCOL,
            daemon_version=self._version,
            wait_s=wait_s,
            capacity=capacity,
            capabilities=CAPABILITIES,
            endpoints=endpoints,
            running=running,
        )
        limit = _SMALL_ANSWER + capacity * _PER_CALL
        document = await self._send("/poll", request.model_dump_json().encode("utf-8"), limit=limit)
        return self._read(OutpostPollAnswer, document)

    async def answer(self, call_id: str, sealed: str) -> OutpostCallAck:
        body = OutpostSealedAnswer(sealed=sealed).model_dump_json().encode("utf-8")
        async with self._one_at_a_time:
            return self._read(OutpostCallAck, await self._send(f"/calls/{call_id}/answer", body))

    async def refuse(self, call_id: str, code: OutpostRefusalCode) -> OutpostCallAck:
        body = OutpostRefusal(code=code).model_dump_json().encode("utf-8")
        async with self._one_at_a_time:
            return self._read(OutpostCallAck, await self._send(f"/calls/{call_id}/refuse", body))

    @staticmethod
    def _read[Answer: (OutpostEnrolmentAnswer, OutpostPollAnswer, OutpostCallAck)](
        model: type[Answer], document: Any
    ) -> Answer:
        try:
            return model.model_validate(document)
        except ValidationError as error:
            raise DoorUnreachableError(f"an answer that is not the contract's {model.__name__}") from error

    def drift(self) -> float | None:
        """How far this machine's clock is from the door's, in seconds, as the last answer showed it:
        positive when the door is ahead. None before any answer."""
        if self.server_time is None:
            return None
        theirs, ours = self.server_time
        return theirs - ours
