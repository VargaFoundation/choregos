# SPDX-License-Identifier: Apache-2.0
"""`CosignVerifier`: a pinned image's signature, verified by the `cosign` binary (S41-14, ADR 0080 §8).

The operator names who may sign: a public key (`--key`, a path or a KMS URI cosign reads), or a
keyless identity, the certificate's subject and its OIDC issuer as regular expressions. The verifier
runs `cosign verify --output json` on `ref@sha256:…` and reads what cosign verified: a signature
counts only when its payload names the very digest that was pinned, so a signature of another image
(a re-tagged one, a copied `.sig`) does not vouch for this one. Keyless, the certificate's subject and
issuer cosign reports are matched again, whole (`re.fullmatch`), against the operator's expressions;
cosign 3 reports none for a bundle signature, so the expressions are passed to it anchored (`^(?:…)$`)
and its exit code is the verdict on the signer.

Every failure is a verdict, never an exception: cosign missing, a timeout, an exit code, an output
that is not cosign's. Publication decides what a refusal means (`CHOREGOS_HOSTED_COSIGN`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from .signatures import SignatureVerdict

#: A command's run: argv in, (exit code, stdout, stderr) out. Cosign's verdict is on stdout, its
#: reasons on stderr; they are read apart.
CosignRunner = Callable[[Sequence[str]], Awaitable[tuple[int, str, str]]]
_PINNED = re.compile(r"^[^@\s]+@(sha256:[0-9a-f]{64})$")
#: What a refusal keeps of cosign's stderr: the end, where it says why.
_REASON_TAIL = 400


def cosign_runner(timeout_s: float) -> CosignRunner:
    """The real runner: the binary as a subprocess, killed past `timeout_s`."""

    async def run(argv: Sequence[str]) -> tuple[int, str, str]:
        try:
            process = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        except FileNotFoundError:
            return 127, "", f"`{argv[0]}` is not installed"
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout_s)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            return 124, "", f"no verdict within {timeout_s:g}s"
        return process.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")

    return run


class CosignVerifier:
    """`ImageVerifier` by `cosign verify`: by key (`key`) or keyless (`identity` and `issuer`)."""

    def __init__(
        self,
        *,
        key: str = "",
        identity: str = "",
        issuer: str = "",
        tlog: bool = True,
        allow_http_registry: bool = False,
        binary: str = "cosign",
        timeout_s: float = 60.0,
        run: CosignRunner | None = None,
    ) -> None:
        if bool(key) == bool(identity or issuer):
            raise ValueError("a cosign verifier takes a public key, or a keyless identity and issuer")
        if not key and not (identity and issuer):
            raise ValueError("a keyless verifier needs both the certificate identity and its OIDC issuer")
        try:
            self._identity = re.compile(identity) if identity else None
            self._issuer = re.compile(issuer) if issuer else None
        except re.error as exc:
            raise ValueError(f"the keyless identity or issuer is not a regular expression: {exc}") from exc
        self._key = key
        self._tlog = tlog
        self._allow_http = allow_http_registry
        self._binary = binary
        self._run = run or cosign_runner(timeout_s)

    def command(self, pinned: str) -> list[str]:
        argv = [self._binary, "verify", "--output", "json"]
        if self._key:
            argv += ["--key", self._key]
        else:
            assert self._identity is not None and self._issuer is not None
            argv += [
                "--certificate-identity-regexp",
                _whole(self._identity.pattern),
                "--certificate-oidc-issuer-regexp",
                _whole(self._issuer.pattern),
            ]
        if not self._tlog:
            argv.append("--insecure-ignore-tlog=true")
        if self._allow_http:
            argv += ["--allow-insecure-registry", "--allow-http-registry"]
        return [*argv, "--", pinned]

    async def verify(self, pinned: str) -> SignatureVerdict:
        found = _PINNED.match(pinned)
        if found is None:
            return SignatureVerdict(False, "only an image pinned by `@sha256:` can be verified")
        digest = found.group(1)
        code, out, err = await self._run(self.command(pinned))
        if code != 0:
            return SignatureVerdict(False, f"cosign refused the signature: {_reason(err, code)}")
        try:
            payloads = json.loads(out)
        except ValueError:
            return SignatureVerdict(False, "cosign's output is not the JSON it prints")
        if not isinstance(payloads, list):
            return SignatureVerdict(False, "cosign's output is not the JSON it prints")
        for payload in payloads:
            signer = self._signer(payload, digest)
            if signer is not None:
                return SignatureVerdict(True, f"signed by {signer}")
        if not any(_signed_digest(payload) == digest for payload in payloads):
            return SignatureVerdict(False, f"no signature names the pinned digest {digest}")
        return SignatureVerdict(False, "no signature is by the configured identity and issuer")

    def _signer(self, payload: Any, digest: str) -> str | None:
        """Who signed `payload`, when it vouches for `digest` and, keyless, for the configured
        identity; `None` otherwise."""
        if _signed_digest(payload) != digest:
            return None
        if self._key:
            return f"the key {self._key}"
        assert self._identity is not None and self._issuer is not None
        optional = payload.get("optional")
        subject = str(optional.get("Subject") or "") if isinstance(optional, dict) else ""
        issuer = str(optional.get("Issuer") or "") if isinstance(optional, dict) else ""
        if subject or issuer:
            # A legacy signature reports its certificate: matched again, whole.
            if self._identity.fullmatch(subject) and self._issuer.fullmatch(issuer):
                return f"{subject} ({issuer})"
            return None
        # A cosign 3 bundle reports no certificate in this JSON (only GIT_* annotations). Cosign
        # itself judged the certificate against the whole-anchored expressions of `command()`, so
        # exit 0 means a signer matching them (proven against the real Sigstore, S41-25).
        return f"an identity matching {self._identity.pattern} ({self._issuer.pattern}), checked by cosign"


def _whole(pattern: str) -> str:
    """`pattern` anchored for cosign, which searches (an unanchored `a@b` accepts `evil-a@b.evil`)."""
    return f"^(?:{pattern})$"


def _reason(err: str, code: int) -> str:
    """Cosign's own words for a refusal: its `Error:` lines, not the warnings around them."""
    lines = [line.strip() for line in err.splitlines() if line.strip()]
    errors = [line.removeprefix("Error: ") for line in lines if line.startswith("Error:")]
    kept = errors or [line for line in lines if not line.startswith("WARNING")][-1:]
    return "; ".join(kept)[-_REASON_TAIL:] or f"exit code {code}"


def _signed_digest(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    critical = payload.get("critical")
    image = critical.get("image") if isinstance(critical, dict) else None
    found = image.get("docker-manifest-digest") if isinstance(image, dict) else None
    return found if isinstance(found, str) else None
