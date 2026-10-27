"""Image references and the registry's digest for a tag (S41-02, ADR 0080 §8)."""

from __future__ import annotations

import hashlib

import httpx
import pytest
from choregos_adapters.base import ImageVerifier, RegistryClient
from choregos_adapters.fakes import FakeImageVerifier, FakeRegistryClient
from choregos_adapters.oci import (
    OciRegistryClient,
    RegistryError,
    UnconfiguredVerifier,
    parse_ref,
    under_prefix,
)

DIGEST = "sha256:" + "a" * 64


@pytest.mark.parametrize(
    ("reference", "registry", "repository", "tag", "digest"),
    [
        ("ghcr.io/acme/triager:1.2", "ghcr.io", "acme/triager", "1.2", None),
        ("ghcr.io/acme/triager", "ghcr.io", "acme/triager", "latest", None),
        (f"ghcr.io/acme/triager@{DIGEST}", "ghcr.io", "acme/triager", None, DIGEST),
        (f"ghcr.io/acme/triager:1.2@{DIGEST}", "ghcr.io", "acme/triager", "1.2", DIGEST),
        ("localhost:5000/triager:dev", "localhost:5000", "triager", "dev", None),
        ("python:3.13", "docker.io", "library/python", "3.13", None),
        ("acme/triager", "docker.io", "acme/triager", "latest", None),
    ],
)
def test_a_reference_is_parsed_as_docker_reads_it(
    reference: str, registry: str, repository: str, tag: str | None, digest: str | None
) -> None:
    ref = parse_ref(reference)
    assert (ref.registry, ref.repository, ref.tag, ref.digest) == (registry, repository, tag, digest)


@pytest.mark.parametrize(
    "reference", ["ghcr.io/Acme/triager:1", "ghcr.io/acme/triager:-x", "ghcr.io/acme/triager@sha256:abc", ""]
)
def test_a_malformed_reference_is_refused(reference: str) -> None:
    with pytest.raises(RegistryError):
        parse_ref(reference)


def test_a_prefix_matches_by_path_segment() -> None:
    prefixes = ["ghcr.io/acme", "registry.internal/"]
    assert under_prefix(parse_ref("ghcr.io/acme/triager:1"), prefixes)
    assert under_prefix(parse_ref("registry.internal/team/agent:1"), prefixes)
    assert not under_prefix(parse_ref("ghcr.io/acme-evil/triager:1"), prefixes)
    assert not under_prefix(parse_ref("ghcr.io/acmeco:1"), prefixes)
    assert not under_prefix(parse_ref("acme/triager:1"), prefixes), "Docker Hub is not ghcr.io"
    assert not under_prefix(parse_ref("ghcr.io/acme/triager:1"), [])


def _registry(*, with_digest_header: bool = True, anonymous: bool = False) -> httpx.MockTransport:
    manifest = b'{"schemaVersion": 2}'
    served = "sha256:" + hashlib.sha256(manifest).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.example":
            assert request.url.params["scope"] == "repository:acme/triager:pull"
            return httpx.Response(200, json={"token": "anonymous-pull"})
        assert request.url.path.startswith("/v2/acme/triager/manifests/")
        if not anonymous and request.headers.get("authorization") != "Bearer anonymous-pull":
            challenge = 'Bearer realm="https://auth.example/token",service="ghcr.io",scope="repository:acme/triager:pull"'
            return httpx.Response(401, headers={"www-authenticate": challenge})
        if request.url.path.endswith("/missing"):
            return httpx.Response(404)
        headers = {"docker-content-digest": served} if with_digest_header else {}
        return httpx.Response(200, headers=headers, content=manifest if request.method == "GET" else b"")

    return httpx.MockTransport(handler)


async def test_the_tag_resolves_through_the_registrys_token_challenge() -> None:
    digest = await OciRegistryClient(transport=_registry()).resolve("ghcr.io/acme/triager:1.2")
    assert digest == "sha256:" + hashlib.sha256(b'{"schemaVersion": 2}').hexdigest()


async def test_without_a_digest_header_the_manifest_is_hashed() -> None:
    client = OciRegistryClient(transport=_registry(with_digest_header=False, anonymous=True))
    digest = await client.resolve("ghcr.io/acme/triager:1.2")
    assert digest == "sha256:" + hashlib.sha256(b'{"schemaVersion": 2}').hexdigest()


async def test_an_unknown_tag_is_refused() -> None:
    with pytest.raises(RegistryError, match="no such image"):
        await OciRegistryClient(transport=_registry()).resolve("ghcr.io/acme/triager:missing")


async def test_a_digest_the_registry_does_not_serve_under_it_is_refused() -> None:
    with pytest.raises(RegistryError, match="serves"):
        await OciRegistryClient(transport=_registry(anonymous=True)).resolve(f"ghcr.io/acme/triager@{DIGEST}")


async def test_a_challenge_that_is_not_https_bearer_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, headers={"www-authenticate": 'Basic realm="ghcr.io"'})

    with pytest.raises(RegistryError, match="credentials"):
        await OciRegistryClient(transport=httpx.MockTransport(handler)).resolve("ghcr.io/acme/triager:1")


async def test_the_fakes_honour_the_protocols_and_moving_tags() -> None:
    registry, verifier = FakeRegistryClient(strict=True), FakeImageVerifier()
    assert isinstance(registry, RegistryClient) and isinstance(verifier, ImageVerifier)
    assert isinstance(UnconfiguredVerifier(), ImageVerifier)
    first = registry.push("ghcr.io/acme/triager:1", "one")
    assert await registry.resolve("ghcr.io/acme/triager:1") == first
    second = registry.push("ghcr.io/acme/triager:1", "two")
    assert await registry.resolve("ghcr.io/acme/triager:1") == second != first
    with pytest.raises(RegistryError):
        await registry.resolve("ghcr.io/acme/ghost:1")
    pinned = f"ghcr.io/acme/triager@{second}"
    assert not (await verifier.verify(pinned)).verified
    verifier.sign(pinned)
    assert (await verifier.verify(pinned)).verified
    assert not (await UnconfiguredVerifier().verify(pinned)).verified


# ───────── an artifact's layer (ADR 0093: plugins and their indexes) ─────────

PLUGIN_TYPE = "application/vnd.choregos.plugin.v1.tar+gzip"


def _artifact_registry(
    blob: bytes, *, layer_digest: str | None = None, media_type: str = PLUGIN_TYPE
) -> httpx.MockTransport:
    import json

    layer = layer_digest or "sha256:" + hashlib.sha256(blob).hexdigest()
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "layers": [{"mediaType": media_type, "digest": layer, "size": len(blob)}],
        }
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/acme/hello/manifests/1.0.0":
            if request.headers.get("authorization") != "Bearer t0k":
                return httpx.Response(
                    401, headers={"www-authenticate": 'Bearer realm="https://ghcr.io/token",scope="pull"'}
                )
            return httpx.Response(200, content=manifest)
        if request.url.path == "/token":
            return httpx.Response(200, json={"token": "t0k"})
        if request.url.path == f"/v2/acme/hello/blobs/{layer}":
            return httpx.Response(307, headers={"location": "https://pkg-containers.githubusercontent.com/b"})
        if request.url.host == "pkg-containers.githubusercontent.com":
            assert "authorization" not in request.headers  # the token stays with the registry
            return httpx.Response(200, content=blob)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_an_artifact_layer_is_pulled_through_the_blob_redirect() -> None:
    client = OciRegistryClient(transport=_artifact_registry(b"plugin bytes"))
    manifest, blob = await client.pull_artifact("ghcr.io/acme/hello:1.0.0", PLUGIN_TYPE, 1024)
    assert blob == b"plugin bytes" and manifest.startswith("sha256:")


async def test_a_layer_whose_bytes_differ_from_the_manifest_is_refused() -> None:
    client = OciRegistryClient(
        transport=_artifact_registry(b"plugin bytes", layer_digest="sha256:" + "0" * 64)
    )
    with pytest.raises(RegistryError, match="do not match"):
        await client.pull_artifact("ghcr.io/acme/hello:1.0.0", PLUGIN_TYPE, 1024)


async def test_an_artifact_without_the_layer_type_or_too_large_is_refused() -> None:
    with pytest.raises(RegistryError, match="no single"):
        await OciRegistryClient(transport=_artifact_registry(b"x", media_type="text/plain")).pull_artifact(
            "ghcr.io/acme/hello:1.0.0", PLUGIN_TYPE, 1024
        )
    with pytest.raises(RegistryError, match="larger"):
        await OciRegistryClient(transport=_artifact_registry(b"x" * 100)).pull_artifact(
            "ghcr.io/acme/hello:1.0.0", PLUGIN_TYPE, 10
        )


async def test_a_manifest_pinned_by_another_digest_is_refused() -> None:
    client = OciRegistryClient(transport=_artifact_registry(b"plugin bytes"))
    with pytest.raises(RegistryError):
        await client.pull_artifact(f"ghcr.io/acme/hello:1.0.0@{DIGEST}", PLUGIN_TYPE, 1024)
