# SPDX-License-Identifier: Apache-2.0
"""A call, before the daemon opens it: who signed it (ADR 0148 §13, `x-call-signature`).

Sealing hides a call from whoever is not its recipient. It does not say who sealed it: anyone who
knows the instance's public sealing key can seal to it, and that key crossed the organisation's
outbound proxy at enrolment. So a call is signed by the door, and an outpost makes only the calls
signed by a key its own policy file pins.

`signed_by` is the whole check, in the contract's order, and it runs before the envelope is
opened: the protected header is the contract's three members and its `kid` is one of the pinned
keys, compared as written; the signature verifies under that key; the claims are this call, this
instance and the digest of this envelope, and no other. It answers the pinned key, or `None`. A
`None` is one refusal, `signature_refused`, which names the class and never the check that failed.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence

import jwt
from choregos_contracts.outpost import (
    SIGNATURE_ALGORITHM,
    OutpostSealedCall,
    call_signature_claims,
    call_signature_header,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def signed_by(handed: OutpostSealedCall, *, pinned: Sequence[str], instance_id: str) -> str | None:
    """The pinned key that signed this call for this instance, or None: the envelope stays closed."""
    signature = handed.signature
    if not isinstance(signature, str):
        return None
    try:
        header = jwt.get_unverified_header(signature)
    except jwt.InvalidTokenError:
        return None
    key = header.get("kid")
    # Nothing is verified under a key the policy file does not pin: the name is looked up first.
    if not isinstance(key, str) or key not in pinned or header != call_signature_header(key):
        return None
    try:
        public = Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)))
        claims = jwt.decode(signature, public, algorithms=[SIGNATURE_ALGORITHM])
    except (jwt.InvalidTokenError, ValueError):
        return None
    expected = call_signature_claims(call_id=handed.id, instance_id=instance_id, sealed=handed.sealed)
    return key if claims == expected else None
