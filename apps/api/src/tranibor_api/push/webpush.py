# SPDX-License-Identifier: Apache-2.0
"""The Web Push wire format, over `cryptography` alone (ADR 0194 §8).

- RFC 8291: the payload is encrypted for the device with its `p256dh` and `auth` secret;
- RFC 8188: the `aes128gcm` content coding, in one record;
- RFC 8292: the request proves who sends with a VAPID JWT (ES256) signed by the installation's key.

No state, no I/O: `encrypt` and `vapid_headers` are pure, so a test decrypts what they produce.
"""

from __future__ import annotations

import base64
import os
import struct
import time
from dataclasses import dataclass

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

#: The record size announced in the header; our payloads are far smaller, so one record carries all.
RECORD_SIZE = 4096
#: A push service accepts 4096 bytes of body at least: 86 of them are the header and the tag.
MAX_PLAINTEXT = RECORD_SIZE - 86
#: A VAPID token lives 12 hours at most here (24 is the protocol's limit).
VAPID_TTL_SECONDS = 12 * 3600


class PushCryptoError(ValueError):
    """A key or a payload that the protocol cannot carry."""


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError as error:
        raise PushCryptoError("not base64url") from error


def _uncompressed(public: ec.EllipticCurvePublicKey) -> bytes:
    return public.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def device_key(p256dh: str) -> ec.EllipticCurvePublicKey:
    """The device's public key: 65 bytes, an uncompressed point of P-256."""
    raw = unb64u(p256dh)
    if len(raw) != 65 or raw[0] != 4:
        raise PushCryptoError("p256dh is not an uncompressed P-256 point")
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except ValueError as error:
        raise PushCryptoError("p256dh is not a point of P-256") from error


def auth_secret(auth: str) -> bytes:
    raw = unb64u(auth)
    if len(raw) != 16:
        raise PushCryptoError("auth is not 16 bytes")
    return raw


def check_subscription_keys(p256dh: str, auth: str) -> None:
    """Raises `PushCryptoError` unless the pair is one the protocol can encrypt for."""
    device_key(p256dh)
    auth_secret(auth)


def _hkdf(salt: bytes, info: bytes, length: int, material: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(material)


def encrypt(
    plaintext: bytes,
    p256dh: str,
    auth: str,
    *,
    sender_key: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """The body of the push request: the `aes128gcm` record of RFC 8291 §3.4 for this device.

    `sender_key` and `salt` are drawn fresh at every call; they are parameters so that the
    specification's own example (RFC 8291, appendix A) can be replayed byte for byte."""
    if len(plaintext) > MAX_PLAINTEXT:
        raise PushCryptoError(f"payload of {len(plaintext)} bytes exceeds {MAX_PLAINTEXT}")
    ua_public = device_key(p256dh)
    secret = auth_secret(auth)
    sender = sender_key or ec.generate_private_key(ec.SECP256R1())
    salt = salt if salt is not None else os.urandom(16)
    if len(salt) != 16:
        raise PushCryptoError("salt is not 16 bytes")
    ua_raw, as_raw = _uncompressed(ua_public), _uncompressed(sender.public_key())
    shared = sender.exchange(ec.ECDH(), ua_public)
    ikm = _hkdf(secret, b"WebPush: info\x00" + ua_raw + as_raw, 32, shared)
    cek = _hkdf(salt, b"Content-Encoding: aes128gcm\x00", 16, ikm)
    nonce = _hkdf(salt, b"Content-Encoding: nonce\x00", 12, ikm)
    # 0x02 closes the last (here the only) record: RFC 8188 §2.
    sealed = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    return salt + struct.pack(">IB", RECORD_SIZE, len(as_raw)) + as_raw + sealed


def decrypt(body: bytes, device_private: ec.EllipticCurvePrivateKey, auth: str) -> bytes:
    """What the device does: the reverse of `encrypt`. Used by the tests, never by the platform."""
    salt, size, key_length = body[:16], struct.unpack(">I", body[16:20])[0], body[20]
    as_raw, sealed = body[21 : 21 + key_length], body[21 + key_length :]
    if size < len(sealed):
        raise PushCryptoError("more than one record")
    sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_raw)
    shared = device_private.exchange(ec.ECDH(), sender)
    ua_raw = _uncompressed(device_private.public_key())
    ikm = _hkdf(auth_secret(auth), b"WebPush: info\x00" + ua_raw + as_raw, 32, shared)
    cek = _hkdf(salt, b"Content-Encoding: aes128gcm\x00", 16, ikm)
    nonce = _hkdf(salt, b"Content-Encoding: nonce\x00", 12, ikm)
    padded = AESGCM(cek).decrypt(nonce, sealed, None)
    # The delimiter (0x02, last record) closes the data; any zero after it is padding.
    return padded.rstrip(b"\x00")[:-1]


@dataclass(frozen=True)
class VapidKey:
    """The installation's application-server key (RFC 8292)."""

    private: ec.EllipticCurvePrivateKey

    @classmethod
    def load(cls, text: str) -> VapidKey:
        """A PEM private key, or the 32 raw bytes of the scalar in base64url (what `web-push` prints)."""
        text = text.strip()
        try:
            if text.startswith("-----"):
                key = serialization.load_pem_private_key(text.encode(), password=None)
            else:
                scalar = unb64u(text)
                if len(scalar) != 32:
                    raise ValueError("a P-256 scalar is 32 bytes")
                key = ec.derive_private_key(int.from_bytes(scalar, "big"), ec.SECP256R1())
        except (ValueError, TypeError) as error:
            raise PushCryptoError(
                "the VAPID private key is neither a PEM nor 32 bytes of base64url"
            ) from error
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise PushCryptoError("the VAPID private key is not a P-256 key")
        return cls(key)

    @property
    def public_key(self) -> str:
        """What the browser passes as `applicationServerKey`: the 65-byte point, base64url."""
        return b64u(_uncompressed(self.private.public_key()))

    def headers(self, audience: str, subject: str, *, now: float | None = None) -> dict[str, str]:
        """`Authorization: vapid t=<jwt>, k=<key>` (RFC 8292 §3); `audience` is the push service's origin."""
        issued = int(now if now is not None else time.time())
        token = jwt.encode(
            {"aud": audience, "exp": issued + VAPID_TTL_SECONDS, "sub": subject},
            self.private,
            algorithm="ES256",
        )
        return {"Authorization": f"vapid t={token}, k={self.public_key}"}
