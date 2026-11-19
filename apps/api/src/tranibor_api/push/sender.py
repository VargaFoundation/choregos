# SPDX-License-Identifier: Apache-2.0
"""The seam to a push service: `PushSender`, its HTTP implementation and its fake (ADR 0005, ADR 0194)."""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from ..config import Settings
from ..logging import get_logger
from .endpoints import check_endpoint, origin_of
from .webpush import PushCryptoError, VapidKey, encrypt

log = get_logger(__name__)

#: How long the push service keeps a message for a device that is off: a decision awaiting for a day is
#: still news, a week-old one is not.
TTL_SECONDS = 86400
SEND_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class Target:
    """One device: where to send and what to encrypt for."""

    endpoint: str
    p256dh: str
    auth: str


class PushSender(Protocol):
    async def send(self, target: Target, payload: dict[str, Any], *, urgency: str = "normal") -> int:
        """The push service's HTTP status (201 accepted, 404/410 the device is gone). Raises on a
        transport failure: the caller counts it, it never kills the sender."""


class HttpPushSender:
    """RFC 8030 over `httpx`, no redirect followed, the VAPID identity of the installation."""

    def __init__(
        self,
        key: VapidKey,
        subject: str,
        *,
        allow_private: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = key
        self._subject = subject
        self._allow_private = allow_private
        self._transport = transport

    async def send(self, target: Target, payload: dict[str, Any], *, urgency: str = "normal") -> int:
        # Checked again at the send: the host may have changed its answer since the registration.
        await check_endpoint(target.endpoint, allow_private=self._allow_private)
        body = encrypt(json.dumps(payload, separators=(",", ":")).encode(), target.p256dh, target.auth)
        headers = {
            **self._key.headers(origin_of(target.endpoint), self._subject),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(TTL_SECONDS),
            "Urgency": urgency,
        }
        async with httpx.AsyncClient(
            timeout=SEND_TIMEOUT_SECONDS, follow_redirects=False, transport=self._transport
        ) as client:
            response = await client.post(target.endpoint, content=body, headers=headers)
        return response.status_code


@dataclass
class FakePushSender:
    """`CHOREGOS_FAKES=1`: nothing leaves, everything is kept for a test to read. A test scripts a
    device's answer with `answers[endpoint] = 410`, or a transport failure with `broken`."""

    sent: list[tuple[Target, dict[str, Any], str]] = field(default_factory=list)
    answers: dict[str, int] = field(default_factory=dict)
    broken: set[str] = field(default_factory=set)

    async def send(self, target: Target, payload: dict[str, Any], *, urgency: str = "normal") -> int:
        if target.endpoint in self.broken:
            raise httpx.ConnectError("scripted transport failure")
        status = self.answers.get(target.endpoint, 201)
        if status < 400:
            self.sent.append((target, payload, urgency))
        return status

    def to(self, endpoint: str) -> list[dict[str, Any]]:
        return [payload for target, payload, _ in self.sent if target.endpoint == endpoint]


_fake = FakePushSender()
#: A test's own sender (a real `HttpPushSender` over a mock transport, say).
_override: PushSender | None = None


def fake_sender() -> FakePushSender:
    return _fake


def vapid_key(settings: Settings) -> VapidKey | None:
    """The installation's key, or `None` when it has none (the feature is then off). A key that
    cannot be read is the operator's mistake: it raises, loudly, instead of silently disabling."""
    if not settings.push_vapid_private_key.strip():
        return None
    if not settings.push_vapid_subject.strip():
        # RFC 8292 §2.1: the push services ask who to contact; some (Apple's) refuse a token without.
        log.warning("push.vapid_subject_missing", detail="CHOREGOS_PUSH_VAPID_SUBJECT is empty: push is off")
        return None
    return VapidKey.load(settings.push_vapid_private_key)


def configured(settings: Settings) -> bool:
    return _override is not None or settings.fakes or vapid_key(settings) is not None


def public_key(settings: Settings) -> str | None:
    """What the phone subscribes with. Under the fakes, a key of the process, so the route answers."""
    key = vapid_key(settings)
    if key is not None:
        return key.public_key
    return _fake_key().public_key if settings.fakes or _override is not None else None


@functools.cache
def _fake_key() -> VapidKey:
    from cryptography.hazmat.primitives.asymmetric import ec

    return VapidKey(ec.generate_private_key(ec.SECP256R1()))


def get_sender(settings: Settings) -> PushSender | None:
    """The sender of this process: a test's, the fake's under `CHOREGOS_FAKES`, the HTTP one when the
    installation has a key, none otherwise."""
    if _override is not None:
        return _override
    key = vapid_key(settings)
    if key is not None:
        return HttpPushSender(
            key, settings.push_vapid_subject, allow_private=settings.push_allow_private_endpoints
        )
    return _fake if settings.fakes else None


__all__ = [
    "FakePushSender",
    "HttpPushSender",
    "PushCryptoError",
    "PushSender",
    "Target",
    "configured",
    "fake_sender",
    "get_sender",
    "public_key",
    "vapid_key",
]
