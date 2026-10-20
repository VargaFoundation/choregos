# SPDX-License-Identifier: Apache-2.0
"""Signature verdicts on a pinned image (ADR 0080 §8).

A verifier answers whether `ref@sha256:…` carries a signature the operator's identities made. The
real one is `CosignVerifier` (`cosign.py`, by key or keyless); until it is configured,
`UnconfiguredVerifier` says so, and a deployment that enforces signatures refuses every image rather
than trust one.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SignatureVerdict:
    verified: bool
    #: Who signed it when verified; why not otherwise. Shown to the publisher, kept in the audit.
    detail: str


class UnconfiguredVerifier:
    """`ImageVerifier` of a deployment that names no signing identity, or names one wrongly:
    nothing verifies, and `detail` says why."""

    def __init__(
        self, detail: str = "no signature verifier is configured (CHOREGOS_HOSTED_COSIGN_*)"
    ) -> None:
        self.detail = detail

    async def verify(self, pinned: str) -> SignatureVerdict:
        return SignatureVerdict(False, self.detail)
