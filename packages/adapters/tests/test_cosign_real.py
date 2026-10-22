"""`CosignVerifier` against the real `cosign` and a real registry (S41-14): the job `hosted-cosign`.

Skipped unless `CHOREGOS_COSIGN_REAL_REGISTRY` names a registry served over HTTP (`localhost:5000`,
`registry:2` in CI) and `cosign` and `crane` are on the path. Everything else is made here, at test
time: the key pairs (no key is ever committed), two tiny images, a signature of one of them, and the
same signature copied onto the other image's signature tag.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from choregos_adapters.oci import CosignVerifier

REGISTRY = os.environ.get("CHOREGOS_COSIGN_REAL_REGISTRY", "")

pytestmark = [
    pytest.mark.cosign_real,
    pytest.mark.skipif(
        not REGISTRY or shutil.which("cosign") is None or shutil.which("crane") is None,
        reason="no registry (CHOREGOS_COSIGN_REAL_REGISTRY), or no cosign or crane on the path",
    ),
]


@dataclass(frozen=True)
class Signed:
    key: Path
    other_key: Path
    signed: str
    unsigned: str
    altered: str
    bundled: str


def _run(*argv: str, cwd: Path | None = None) -> str:
    env = {**os.environ, "COSIGN_PASSWORD": ""}
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=120)
    assert done.returncode == 0, f"{argv}: {done.stderr}"
    return done.stdout.strip()


def _image(tmp: Path, tag: str, content: bytes) -> str:
    """A one-layer image pushed as `<registry>/s41-14/tiny:<tag>`, returned pinned by its digest."""
    layer = tmp / f"{tag}.tar"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo("hello.txt")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    layer.write_bytes(buffer.getvalue())
    name = f"{REGISTRY}/s41-14/tiny"
    _run("crane", "append", "--insecure", "-f", str(layer), "-t", f"{name}:{tag}")
    return f"{name}@{_run('crane', 'digest', '--insecure', f'{name}:{tag}')}"


@pytest.fixture(scope="module")
def signed(tmp_path_factory: pytest.TempPathFactory) -> Signed:
    tmp = tmp_path_factory.mktemp("cosign")
    # Fresh bytes on every run: a registry kept between runs holds no signature of these digests.
    run = uuid.uuid4().hex.encode()
    for name in ("trusted", "other"):
        (tmp / name).mkdir()
        _run("cosign", "generate-key-pair", cwd=tmp / name)
    signed = _image(tmp, "signed", b"the image the operator's key signed " + run)
    unsigned = _image(tmp, "unsigned", b"an image nobody signed " + run)
    altered = _image(tmp, "altered", b"other bytes under the signed image's signature " + run)
    bundled = _image(tmp, "bundled", b"an image signed in cosign 3's bundle format " + run)
    # Without Rekor, cosign 3 signs only outside a signing config. `signed` gets the `.sig` tag
    # format, the one a signature can be copied by; `bundled` cosign 3's default, a Sigstore bundle
    # attached as an OCI referrer.
    sign = (
        "cosign", "sign", "--yes", "--key", str(tmp / "trusted" / "cosign.key"), "--tlog-upload=false",
        "--use-signing-config=false", "--allow-http-registry", "--allow-insecure-registry",
    )  # fmt: skip
    _run(*sign, "--new-bundle-format=false", signed)
    _run(*sign, bundled)
    # The signature of `signed`, copied to the tag cosign reads for `altered`: valid by the key,
    # about other bytes.
    name, _, digest = signed.partition("@")
    altered_digest = altered.partition("@")[2]
    _run(
        "crane", "copy", "--insecure",
        f"{name}:{digest.replace(':', '-')}.sig", f"{name}:{altered_digest.replace(':', '-')}.sig",
    )  # fmt: skip
    return Signed(
        tmp / "trusted" / "cosign.pub", tmp / "other" / "cosign.pub", signed, unsigned, altered, bundled
    )


def _verifier(key: Path) -> CosignVerifier:
    return CosignVerifier(key=str(key), tlog=False, allow_http_registry=True, timeout_s=60)


async def test_the_image_the_key_signed_is_accepted(signed: Signed) -> None:
    verdict = await _verifier(signed.key).verify(signed.signed)
    assert verdict.verified, verdict.detail


async def test_an_unsigned_image_is_refused(signed: Signed) -> None:
    verdict = await _verifier(signed.key).verify(signed.unsigned)
    assert not verdict.verified
    assert "no signatures found" in verdict.detail, verdict.detail


async def test_a_signature_copied_onto_other_bytes_is_refused(signed: Signed) -> None:
    verdict = await _verifier(signed.key).verify(signed.altered)
    assert not verdict.verified
    assert "invalid or missing digest in claim" in verdict.detail, verdict.detail


async def test_a_signature_by_another_key_is_refused(signed: Signed) -> None:
    verdict = await _verifier(signed.other_key).verify(signed.signed)
    assert not verdict.verified
    assert "invalid signature" in verdict.detail, verdict.detail


async def test_an_image_signed_in_the_bundle_format_is_accepted(signed: Signed) -> None:
    verdict = await _verifier(signed.key).verify(signed.bundled)
    assert verdict.verified, verdict.detail


async def test_a_bundle_by_another_key_is_refused(signed: Signed) -> None:
    verdict = await _verifier(signed.other_key).verify(signed.bundled)
    assert not verdict.verified
