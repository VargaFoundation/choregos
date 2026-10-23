"""`CosignVerifier` keyless, against the real Sigstore (S41-25): the job `hosted-cosign`.

Needs the network and `cosign`; skipped unless `CHOREGOS_COSIGN_KEYLESS_REAL=1` (the job sets it and
fails on a skip). The image is cosign v3.1.3 itself, signed keyless by the Sigstore project's own
service account: a Sigstore bundle, whose `cosign verify --output json` reports no certificate
subject or issuer. Before S41-25 every such signature was refused by the verifier.
"""

from __future__ import annotations

import os
import shutil

import pytest
from choregos_adapters.oci import CosignVerifier

pytestmark = [
    pytest.mark.cosign_real,
    pytest.mark.skipif(
        os.environ.get("CHOREGOS_COSIGN_KEYLESS_REAL") != "1" or shutil.which("cosign") is None,
        reason="no network test requested (CHOREGOS_COSIGN_KEYLESS_REAL=1), or no cosign on the path",
    ),
]

IMAGE = (
    "ghcr.io/sigstore/cosign/cosign:v3.1.3"
    "@sha256:9e5c2f2edc34351160407ca3416c61855bdf9403c3c5936e0f0be7fc261611b8"
)
IDENTITY = "keyless@projectsigstore.iam.gserviceaccount.com"
ISSUER = "https://accounts.google.com"


def _verifier(identity: str = IDENTITY, issuer: str = ISSUER) -> CosignVerifier:
    return CosignVerifier(identity=identity, issuer=issuer, timeout_s=90)


async def test_the_right_identity_and_issuer_are_accepted() -> None:
    verdict = await _verifier().verify(IMAGE)
    assert verdict.verified, verdict.detail


async def test_a_regular_expression_for_the_identity_is_accepted() -> None:
    verdict = await _verifier(
        r".+@projectsigstore\.iam\.gserviceaccount\.com", r"https://accounts\.google\.com"
    ).verify(IMAGE)
    assert verdict.verified, verdict.detail


@pytest.mark.parametrize(
    "identity",
    [
        "someone-else@example.com",
        # A part of the identity is not the identity: cosign searches unless the expression is anchored.
        "keyless@projectsigstore",
        "projectsigstore.iam.gserviceaccount.com",
    ],
)
async def test_another_identity_is_refused(identity: str) -> None:
    verdict = await _verifier(identity=identity).verify(IMAGE)
    assert not verdict.verified, verdict.detail


@pytest.mark.parametrize("issuer", ["https://token.actions.githubusercontent.com", "https://accounts"])
async def test_another_issuer_is_refused(issuer: str) -> None:
    verdict = await _verifier(issuer=issuer).verify(IMAGE)
    assert not verdict.verified, verdict.detail
