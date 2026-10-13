# SPDX-License-Identifier: Apache-2.0
"""A run's assignment sealed to ONE warm pod (S35-02, ADR 0053).

The `agent_sandbox` executor gives a warm runner its run — the run token, the deployment-wide
credentials, the environment — over the pod network. In clear, anyone who can read that
network reads the token. Sealed, only the pod that will run it can open it:

1. at boot, the warm runner makes an X25519 key pair in memory and prints the public key on
   ONE line of its stdout (`KEY_LINE_PREFIX`);
2. the executor reads that line through the API server (`pods/log`): the API server reaches the
   kubelet over authenticated TLS, so nobody on the pod network can swap the key;
3. it seals the assignment to that key — an ephemeral X25519 exchange, HKDF-SHA256, then
   ChaCha20-Poly1305 with the run id as associated data — and pushes only the ciphertext;
4. the pod opens it in memory and drops its private key: it can open one assignment, ever.

`cryptography` is already a dependency of this package (the run token's ES256); nothing new.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

#: The one line a warm runner prints at boot; the executor looks for it in the pod's log.
KEY_LINE_PREFIX = "choregos-assignment-key: "
_INFO = b"choregos/assignment/v1"
_RAW = serialization.Encoding.Raw
_RAW_PUBLIC = serialization.PublicFormat.Raw


class SealError(ValueError):
    """A sealed assignment that does not open: not sealed to this key, altered, or another run's."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_key() -> tuple[X25519PrivateKey, str]:
    """A key pair for one warm pod: the private key (memory only) and the public key to print."""
    private = X25519PrivateKey.generate()
    return private, _b64(private.public_key().public_bytes(_RAW, _RAW_PUBLIC))


def key_line(public_key: str) -> str:
    return f"{KEY_LINE_PREFIX}{public_key}"


def key_from_log(log: str) -> str | None:
    """The public key a runner printed at boot, or None if it printed none (yet).

    The FIRST such line counts: the runner prints it before it listens, and anything printed
    later — by the agent, once the run started — cannot replace it."""
    for line in log.splitlines():
        if line.startswith(KEY_LINE_PREFIX):
            key = line[len(KEY_LINE_PREFIX) :].strip()
            return key or None
    return None


def _key(shared: bytes, ephemeral: bytes, recipient: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=ephemeral + recipient, info=_INFO).derive(shared)


def seal(public_key: str, run_id: str, secret: dict[str, Any]) -> str:
    """`secret` sealed to `public_key`, bound to `run_id`; only that pod's private key opens it."""
    recipient_bytes = _unb64(public_key)
    recipient = X25519PublicKey.from_public_bytes(recipient_bytes)
    ephemeral = X25519PrivateKey.generate()
    ephemeral_bytes = ephemeral.public_key().public_bytes(_RAW, _RAW_PUBLIC)
    key = _key(ephemeral.exchange(recipient), ephemeral_bytes, recipient_bytes)
    nonce = os.urandom(12)
    sealed = ChaCha20Poly1305(key).encrypt(nonce, json.dumps(secret).encode(), run_id.encode())
    return _b64(ephemeral_bytes + nonce + sealed)


def open_sealed(private: X25519PrivateKey, run_id: str, sealed: str) -> dict[str, Any]:
    """Opens what `seal` made for this key and this run; raises `SealError` otherwise."""
    try:
        raw = _unb64(sealed)
        ephemeral_bytes, nonce, ciphertext = raw[:32], raw[32:44], raw[44:]
        recipient_bytes = private.public_key().public_bytes(_RAW, _RAW_PUBLIC)
        shared = private.exchange(X25519PublicKey.from_public_bytes(ephemeral_bytes))
        key = _key(shared, ephemeral_bytes, recipient_bytes)
        data: Any = json.loads(ChaCha20Poly1305(key).decrypt(nonce, ciphertext, run_id.encode()))
    except Exception as exc:  # InvalidTag, a short or non-base64 body, bad JSON: all the same
        raise SealError("the assignment does not open with this pod's key for this run") from exc
    if not isinstance(data, dict):
        raise SealError("a sealed assignment is a JSON object")
    return data
