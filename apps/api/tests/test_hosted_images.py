"""A hosted agent's image at publication (S41-02, ADR 0080 §8): under the operator's allowlist, pinned
to the registry's digest, signed when the deployment says so."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from choregos_adapters.fakes import FakeImageVerifier, FakeRegistryClient
from httpx import AsyncClient

AGENTS = "/api/v1/orgs/varga/agents"
REF = "ghcr.io/acme/triager:1.2"
FORGED = "sha256:" + "f" * 64


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FakeRegistryClient, FakeImageVerifier]]:
    from choregos_api.config import reset_settings_cache
    from choregos_api.services.hosted_images import set_image_services

    monkeypatch.setenv("CHOREGOS_HOSTED_REGISTRIES", json.dumps(["ghcr.io/acme"]))
    reset_settings_cache()
    client, verifier = FakeRegistryClient(strict=True), FakeImageVerifier()
    set_image_services(client, verifier)
    yield client, verifier
    set_image_services(None)
    reset_settings_cache()


def _cosign(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_HOSTED_COSIGN", mode)
    reset_settings_cache()


def _spec(ref: str = REF, digest: str | None = None) -> dict[str, Any]:
    source: dict[str, Any] = {"type": "image", "ref": ref}
    if digest is not None:
        source["digest"] = digest
    return {"instructions": "Answer {{ ticket.title }}.", "runtime": {"kind": "hosted", "source": source}}


async def _create(client: AsyncClient, spec: dict[str, Any]) -> Any:
    return await client.post(AGENTS, json={"slug": "triager", "display_name": "Triager", "spec": spec})


async def _source(client: AsyncClient, version: int) -> dict[str, Any]:
    found = (await client.get(f"{AGENTS}/triager/versions/{version}")).json()
    source: dict[str, Any] = found["spec"]["runtime"]["source"]
    return source


async def test_without_an_allowlist_no_image_is_accepted(client: AsyncClient, admin: str) -> None:
    refused = await _create(client, _spec())
    assert refused.status_code == 422, refused.text
    error = refused.json()["errors"][0]
    assert error["loc"] == ["runtime", "source", "ref"]
    assert "only zip sources" in error["msg"]


@pytest.mark.parametrize(
    "ref", ["ghcr.io/acme-evil/triager:1", "docker.io/acme/triager:1", "acme/triager:1", "ghcr.io/acmeco:1"]
)
async def test_an_image_outside_the_allowlist_is_refused(
    client: AsyncClient, admin: str, registry: Any, ref: str
) -> None:
    refused = await _create(client, _spec(ref))
    assert refused.status_code == 422, refused.text
    assert "under no allowed prefix" in refused.text
    assert registry[0].resolved == [], "nothing is fetched for a refused reference"


async def test_the_tag_is_pinned_to_the_registrys_digest(
    client: AsyncClient, admin: str, registry: Any
) -> None:
    first = registry[0].push(REF, "image one")
    created = await _create(client, _spec())
    assert created.status_code == 201, created.text
    assert await _source(client, 1) == {"type": "image", "ref": REF, "digest": first}

    moved = registry[0].push(REF, "image two")
    assert moved != first
    published = await client.post(
        f"{AGENTS}/triager/versions/publish",
        json={"spec": _spec(), "message": "New build", "base_version": 1},
    )
    assert published.status_code == 201, published.text
    assert (await _source(client, 1))["digest"] == first, "a published version keeps its bytes"
    assert (await _source(client, 2))["digest"] == moved


async def test_a_digest_the_caller_wrote_is_resolved_again(
    client: AsyncClient, admin: str, registry: Any
) -> None:
    served = registry[0].push(REF, "the real image")
    created = await _create(client, _spec(digest=FORGED))
    assert created.status_code == 201, created.text
    assert (await _source(client, 1))["digest"] == served


async def test_a_restore_keeps_the_digest_it_was_published_with(
    client: AsyncClient, admin: str, registry: Any
) -> None:
    first = registry[0].push(REF, "image one")
    assert (await _create(client, _spec())).status_code == 201
    registry[0].push(REF, "image two")
    await client.post(
        f"{AGENTS}/triager/versions/publish",
        json={"spec": _spec(), "message": "New build", "base_version": 1},
    )
    restored = await client.post(f"{AGENTS}/triager/versions/1/restore", json={"base_version": 2})
    assert restored.status_code == 201, restored.text
    assert (await _source(client, 3))["digest"] == first


async def test_an_image_the_registry_does_not_have_is_refused(
    client: AsyncClient, admin: str, registry: Any
) -> None:
    refused = await _create(client, _spec("ghcr.io/acme/ghost:1"))
    assert refused.status_code == 422, refused.text
    assert "no such image" in refused.text


async def test_under_enforce_an_unsigned_image_is_refused_and_a_signed_one_accepted(
    client: AsyncClient, admin: str, registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cosign(monkeypatch, "enforce")
    digest = registry[0].push(REF, "image one")
    refused = await _create(client, _spec())
    assert refused.status_code == 422, refused.text
    assert "no trusted signature" in refused.text

    registry[1].sign(f"ghcr.io/acme/triager@{digest}")
    created = await _create(client, _spec())
    assert created.status_code == 201, created.text
    assert registry[1].verified[-1] == f"ghcr.io/acme/triager@{digest}", "the pinned image is verified"


async def test_under_warn_an_unsigned_image_is_published_and_logged(
    client: AsyncClient, admin: str, registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cosign(monkeypatch, "warn")
    registry[0].push(REF, "image one")
    with structlog.testing.capture_logs() as logs:
        created = await _create(client, _spec())
    assert created.status_code == 201, created.text
    assert any(entry["event"] == "hosted.signature_unverified" for entry in logs)


async def test_with_signatures_off_nothing_is_verified(
    client: AsyncClient, admin: str, registry: Any
) -> None:
    registry[0].push(REF, "image one")
    assert (await _create(client, _spec())).status_code == 201
    assert registry[1].verified == []


SUBJECT = "https://github.com/acme/triager/.github/workflows/release.yml@refs/tags/v1.2"
ISSUER = "https://token.actions.githubusercontent.com"


def _cosign_answering(case: str) -> Any:
    """`cosign verify` as a test writes it (S41-14): its answer for the pinned reference it is asked."""

    async def run(argv: Any) -> tuple[int, str, str]:
        digest = argv[-1].partition("@")[2]
        if case == "unsigned":
            return 10, "", "Error: no signatures found"
        signed = FORGED if case == "wrong_digest" else digest
        subject = (
            "https://github.com/evil/triager/.github/workflows/release.yml" if case == "stranger" else SUBJECT
        )
        payload = {
            "critical": {"image": {"docker-manifest-digest": signed}},
            "optional": {"Subject": subject, "Issuer": ISSUER},
        }
        return 0, json.dumps([payload]), ""

    return run


@pytest.mark.parametrize(
    ("case", "refusal"),
    [
        ("unsigned", "no signatures found"),
        ("wrong_digest", "no signature names the pinned digest"),
        ("stranger", "no signature is by the configured identity and issuer"),
        ("signed", None),
    ],
)
async def test_under_enforce_cosigns_verdict_decides_the_publication(
    client: AsyncClient,
    admin: str,
    registry: Any,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    refusal: str | None,
) -> None:
    from choregos_adapters.oci import CosignVerifier
    from choregos_api.services.hosted_images import set_image_services

    _cosign(monkeypatch, "enforce")
    verifier = CosignVerifier(
        identity=r"https://github\.com/acme/triager/\.github/workflows/release\.yml@refs/tags/v.*",
        issuer=r"https://token\.actions\.githubusercontent\.com",
        run=_cosign_answering(case),
    )
    set_image_services(registry[0], verifier)
    digest = registry[0].push(REF, "image one")
    created = await _create(client, _spec())
    if refusal is None:
        assert created.status_code == 201, created.text
        assert (await _source(client, 1))["digest"] == digest
    else:
        assert created.status_code == 422, created.text
        assert "no trusted signature" in created.text and refusal in created.text


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, "no signature verifier is configured"),
        ({"KEY": "/etc/choregos/cosign/cosign.pub"}, None),
        ({"IDENTITY": "https://github.com/acme/.*", "ISSUER": ISSUER}, None),
        ({"IDENTITY": "https://github.com/acme/.*"}, "misconfigured: a keyless verifier needs both"),
        ({"KEY": "cosign.pub", "ISSUER": ISSUER}, "misconfigured: a cosign verifier takes a public key, or"),
        ({"IDENTITY": "(", "ISSUER": ISSUER}, "misconfigured: the keyless identity or issuer is not"),
    ],
)
async def test_the_deployment_names_the_signer_and_a_wrong_name_trusts_nothing(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str | None
) -> None:
    from choregos_adapters.oci import CosignVerifier, UnconfiguredVerifier
    from choregos_api.config import reset_settings_cache
    from choregos_api.services.hosted_images import image_verifier, set_image_services

    monkeypatch.setenv("CHOREGOS_FAKES", "0")
    for name, value in env.items():
        monkeypatch.setenv(f"CHOREGOS_HOSTED_COSIGN_{name}", value)
    reset_settings_cache()
    set_image_services(None)
    try:
        verifier = image_verifier()
        if expected is None:
            assert isinstance(verifier, CosignVerifier)
        else:
            assert isinstance(verifier, UnconfiguredVerifier)
            verdict = await verifier.verify(f"ghcr.io/acme/triager@{FORGED}")
            assert not verdict.verified and expected in verdict.detail
    finally:
        reset_settings_cache()


@pytest.mark.parametrize(("tlog", "skipped"), [("true", False), ("false", True)])
def test_the_deployment_decides_whether_the_transparency_log_is_checked(
    monkeypatch: pytest.MonkeyPatch, tlog: str, skipped: bool
) -> None:
    from choregos_adapters.oci import CosignVerifier
    from choregos_api.config import reset_settings_cache
    from choregos_api.services.hosted_images import image_verifier, set_image_services

    monkeypatch.setenv("CHOREGOS_FAKES", "0")
    monkeypatch.setenv("CHOREGOS_HOSTED_COSIGN_KEY", "/etc/choregos/cosign/cosign.pub")
    monkeypatch.setenv("CHOREGOS_HOSTED_COSIGN_TLOG", tlog)
    reset_settings_cache()
    set_image_services(None)
    try:
        verifier = image_verifier()
        assert isinstance(verifier, CosignVerifier)
        argv = verifier.command(f"ghcr.io/acme/triager@{FORGED}")
        assert ("--insecure-ignore-tlog=true" in argv) is skipped
    finally:
        reset_settings_cache()
