# SPDX-License-Identifier: Apache-2.0
"""`FakeRegistryClient` and `FakeImageVerifier`: an OCI registry and a signature check in memory
(ADR 0005, ADR 0080), for `CHOREGOS_FAKES=1` and tests.

Scriptable: `push` puts an image under a tag (pushing again moves the tag, which is how a test
proves that a version keeps the digest it was published with); `remove` makes a reference unknown;
`sign` marks a pinned image as signed. Without a push, every well-formed reference resolves to a
digest derived from its name and tag, so a demo needs no staging.
"""

from __future__ import annotations

import hashlib

from ..oci import RegistryError, SignatureVerdict, parse_ref


class FakeRegistryClient:
    def __init__(self, *, strict: bool = False) -> None:
        #: `strict`: a reference nobody pushed is unknown, instead of resolving to a derived digest.
        self.strict = strict
        self.tags: dict[str, str] = {}
        self.removed: set[str] = set()
        self.resolved: list[str] = []

    async def resolve(self, reference: str) -> str:
        ref = parse_ref(reference)
        self.resolved.append(reference)
        key = f"{ref.name}:{ref.tag}" if ref.digest is None else f"{ref.name}@{ref.digest}"
        if key in self.removed:
            raise RegistryError(f"`{reference}`: the registry has no such image")
        if ref.digest is not None:
            if self.strict and ref.digest not in self.tags.values():
                raise RegistryError(f"`{reference}`: the registry has no such image")
            return ref.digest
        if key in self.tags:
            return self.tags[key]
        if self.strict:
            raise RegistryError(f"`{reference}`: the registry has no such image")
        return "sha256:" + hashlib.sha256(key.encode()).hexdigest()

    # ───────── staging ─────────

    def push(self, reference: str, content: bytes | str) -> str:
        """Puts `content` under the reference's tag and returns its digest."""
        ref = parse_ref(reference)
        data = content.encode() if isinstance(content, str) else content
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        self.tags[f"{ref.name}:{ref.tag}"] = digest
        return digest

    def remove(self, reference: str) -> None:
        ref = parse_ref(reference)
        self.removed.add(f"{ref.name}:{ref.tag}" if ref.digest is None else f"{ref.name}@{ref.digest}")


class FakeImageVerifier:
    def __init__(self) -> None:
        self.signed: dict[str, str] = {}
        self.verified: list[str] = []

    async def verify(self, pinned: str) -> SignatureVerdict:
        self.verified.append(pinned)
        signer = self.signed.get(pinned)
        if signer is None:
            return SignatureVerdict(False, "no signature by a trusted identity")
        return SignatureVerdict(True, f"signed by {signer}")

    def sign(self, pinned: str, signer: str = "release@acme.example") -> None:
        self.signed[pinned] = signer
