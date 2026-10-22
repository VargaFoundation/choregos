"""`CosignVerifier` (S41-14): cosign's verdict on a pinned image, read rather than trusted. A scripted
runner stands for the binary; `test_cosign_real.py` runs the real one against a registry in CI."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

import pytest
from choregos_adapters.base import ImageVerifier
from choregos_adapters.oci import CosignVerifier, cosign_runner

DIGEST = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
PINNED = f"registry.internal/acme/triager@{DIGEST}"
IDENTITY = r"https://github\.com/acme/triager/\.github/workflows/release\.yml@refs/tags/v.*"
ISSUER = r"https://token\.actions\.githubusercontent\.com"
SUBJECT = "https://github.com/acme/triager/.github/workflows/release.yml@refs/tags/v1.2"


def _payload(digest: str = DIGEST, **optional: str) -> dict[str, Any]:
    return {
        "critical": {
            "identity": {"docker-reference": "registry.internal/acme/triager"},
            "image": {"docker-manifest-digest": digest},
            "type": "cosign container image signature",
        },
        "optional": optional or None,
    }


class Script:
    """`cosign` as a test writes it: one answer, and the argv it was asked."""

    def __init__(self, code: int = 0, out: str = "", err: str = "") -> None:
        self.answer = (code, out, err)
        self.calls: list[list[str]] = []

    async def __call__(self, argv: Sequence[str]) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        return self.answer


def _signed(*payloads: dict[str, Any]) -> Script:
    return Script(0, json.dumps(list(payloads)), "Verification for … The following checks were performed")


async def test_a_signature_by_the_key_on_the_pinned_digest_is_accepted() -> None:
    script = _signed(_payload())
    verifier = CosignVerifier(key="/etc/choregos/cosign/cosign.pub", run=script)
    assert isinstance(verifier, ImageVerifier)
    verdict = await verifier.verify(PINNED)
    assert verdict.verified, verdict.detail
    assert verdict.detail == "signed by the key /etc/choregos/cosign/cosign.pub"
    (argv,) = script.calls
    assert argv[:4] == ["cosign", "verify", "--output", "json"]
    assert argv[argv.index("--key") + 1] == "/etc/choregos/cosign/cosign.pub"
    assert argv[-2:] == ["--", PINNED], "the pinned reference, never a tag, and never read as a flag"
    assert "--insecure-ignore-tlog=true" not in argv, "the transparency log is checked by default"


async def test_an_unsigned_image_is_refused_with_cosigns_reason() -> None:
    script = Script(1, "", "Error: no matching signatures: no signatures found")
    verdict = await CosignVerifier(key="cosign.pub", run=script).verify(PINNED)
    assert not verdict.verified
    assert "no signatures found" in verdict.detail


async def test_a_signature_of_another_digest_does_not_vouch_for_this_one() -> None:
    """A re-tagged or altered image: cosign answered for a payload naming other bytes."""
    verdict = await CosignVerifier(key="cosign.pub", run=_signed(_payload(OTHER))).verify(PINNED)
    assert not verdict.verified
    assert verdict.detail == f"no signature names the pinned digest {DIGEST}"


async def test_one_signature_on_the_digest_among_others_is_enough() -> None:
    verifier = CosignVerifier(key="cosign.pub", run=_signed(_payload(OTHER), _payload()))
    assert (await verifier.verify(PINNED)).verified


async def test_keyless_the_subject_and_the_issuer_are_matched_again() -> None:
    script = _signed(_payload(Subject=SUBJECT, Issuer="https://token.actions.githubusercontent.com"))
    verifier = CosignVerifier(identity=IDENTITY, issuer=ISSUER, run=script)
    verdict = await verifier.verify(PINNED)
    assert verdict.verified, verdict.detail
    assert SUBJECT in verdict.detail
    (argv,) = script.calls
    assert argv[argv.index("--certificate-identity-regexp") + 1] == f"^(?:{IDENTITY})$"
    assert argv[argv.index("--certificate-oidc-issuer-regexp") + 1] == f"^(?:{ISSUER})$"
    assert "--key" not in argv


@pytest.mark.parametrize(
    ("subject", "issuer"),
    [
        ("https://github.com/evil/triager/.github/workflows/release.yml@refs/tags/v1", None),
        # Contains the identity but is not it: matched whole, not searched.
        (f"https://evil.example/?{SUBJECT}", None),
        (SUBJECT, "https://accounts.google.com"),
        ("", None),
    ],
)
async def test_keyless_a_signature_by_another_identity_is_refused(subject: str, issuer: str | None) -> None:
    payload = _payload(Subject=subject, Issuer=issuer or "https://token.actions.githubusercontent.com")
    verifier = CosignVerifier(identity=IDENTITY, issuer=ISSUER, run=_signed(payload))
    verdict = await verifier.verify(PINNED)
    assert not verdict.verified
    assert verdict.detail == "no signature is by the configured identity and issuer"


@pytest.mark.parametrize("out", ["", "not json", '{"critical": {}}', "[1, null]", '[{"critical": 3}]'])
async def test_an_output_that_is_not_cosigns_is_refused(out: str) -> None:
    verdict = await CosignVerifier(key="cosign.pub", run=Script(0, out)).verify(PINNED)
    assert not verdict.verified


@pytest.mark.parametrize(
    "ref", ["registry.internal/acme/triager:1.2", "registry.internal/acme/triager@sha256:abc", f"x @{DIGEST}"]
)
async def test_only_a_pinned_reference_is_verified(ref: str) -> None:
    script = _signed(_payload())
    verdict = await CosignVerifier(key="cosign.pub", run=script).verify(ref)
    assert not verdict.verified and "pinned" in verdict.detail
    assert script.calls == [], "cosign is not asked about a tag"


async def test_without_the_transparency_log_the_flag_says_so() -> None:
    script = _signed(_payload())
    await CosignVerifier(key="cosign.pub", tlog=False, allow_http_registry=True, run=script).verify(PINNED)
    (argv,) = script.calls
    assert "--insecure-ignore-tlog=true" in argv and "--allow-http-registry" in argv


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"key": "cosign.pub", "identity": IDENTITY, "issuer": ISSUER},
        {"identity": IDENTITY},
        {"issuer": ISSUER},
        {"identity": "(", "issuer": ISSUER},
    ],
)
def test_a_verifier_names_exactly_one_kind_of_signer(config: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        CosignVerifier(**config)


async def test_the_real_runner_reports_a_missing_binary_and_a_timeout() -> None:
    missing = CosignVerifier(key="cosign.pub", binary="/nonexistent/cosign")
    verdict = await missing.verify(PINNED)
    assert not verdict.verified and "is not installed" in verdict.detail
    started = time.monotonic()
    code, _, err = await cosign_runner(0.05)(["sleep", "5"])
    assert code == 124 and "no verdict within" in err
    assert time.monotonic() - started < 2, "the process is killed, not waited for"


async def test_a_refusal_keeps_cosigns_error_not_its_warnings() -> None:
    err = (
        "WARNING: Skipping tlog verification is an insecure practice\n"
        "Error: no signatures found\nerror during command execution: no signatures found\n"
    )
    verdict = await CosignVerifier(key="cosign.pub", run=Script(10, "", err)).verify(PINNED)
    assert verdict.detail == "cosign refused the signature: no signatures found"
    silent = await CosignVerifier(key="cosign.pub", run=Script(10, "", "")).verify(PINNED)
    assert silent.detail == "cosign refused the signature: exit code 10"


async def test_keyless_a_cosign_3_bundle_reports_no_certificate_and_cosigns_exit_is_the_verdict() -> None:
    # S41-25: a Sigstore bundle's JSON carries only GIT_* annotations. The identity is enforced by
    # cosign, which gets the expressions anchored: `a@b` must not accept `evil-a@b.evil`.
    script = _signed(_payload(GIT_VERSION="v3.1.3"))
    verdict = await CosignVerifier(identity=IDENTITY, issuer=ISSUER, run=script).verify(PINNED)
    assert verdict.verified, verdict.detail
    (argv,) = script.calls
    assert argv[argv.index("--certificate-identity-regexp") + 1].startswith("^(?:")
    assert argv[argv.index("--certificate-oidc-issuer-regexp") + 1].endswith(")$")


async def test_keyless_a_bundle_signature_of_another_digest_is_still_refused() -> None:
    script = _signed(_payload(OTHER, GIT_VERSION="v3.1.3"))
    verdict = await CosignVerifier(identity=IDENTITY, issuer=ISSUER, run=script).verify(PINNED)
    assert not verdict.verified
