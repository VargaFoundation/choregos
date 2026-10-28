# SPDX-License-Identifier: Apache-2.0
"""Image references and the OCI distribution API: what a hosted agent's image resolves to (ADR 0080).

Publication names an image by a reference a person wrote (`ghcr.io/acme/triager:1.2`); runs pull it
by the digest the registry answered at that moment, never by the tag. Only the manifest is read
(`HEAD /v2/<repository>/manifests/<tag>`), anonymously or with the bearer token the registry's
challenge hands out; nothing is pulled.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import httpx

from ..errors import AdapterError

DOCKER_HUB = "docker.io"
_DOCKER_HUB_API = "registry-1.docker.io"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPOSITORY = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
#: What the manifest request accepts: an index (several platforms) or a single manifest, OCI or Docker.
MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
#: A manifest larger than this is not one; the body is read only when the digest header is missing.
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024


class RegistryError(AdapterError):
    """The reference cannot be resolved: malformed, unknown to the registry, or the registry failed."""


@dataclass(frozen=True)
class ImageRef:
    """A parsed reference: `registry/repository[:tag][@digest]`, with Docker Hub's defaults applied."""

    registry: str
    repository: str
    tag: str | None
    digest: str | None

    @property
    def name(self) -> str:
        """`registry/repository`: what an allowlist prefix is matched against."""
        return f"{self.registry}/{self.repository}"

    def pinned(self, digest: str) -> str:
        """The reference a run pulls: the name and the digest, without the tag."""
        return f"{self.name}@{digest}"

    def __str__(self) -> str:
        return self.name + (f":{self.tag}" if self.tag else "") + (f"@{self.digest}" if self.digest else "")


def parse_ref(reference: str) -> ImageRef:
    """`reference` parsed, or `RegistryError` naming what is wrong. Without a registry host the
    reference is Docker Hub's (`python:3.13` is `docker.io/library/python:3.13`); without a tag or a
    digest it is `latest`, as `docker pull` reads it."""
    rest, _, digest = reference.partition("@")
    if digest and not _DIGEST.match(digest):
        raise RegistryError(f"`{reference}`: the digest is not `sha256:` and 64 hexadecimal characters")
    first, slash, remainder = rest.partition("/")
    if slash and ("." in first or ":" in first or first == "localhost"):
        registry, path = first, remainder
    else:
        registry, path = DOCKER_HUB, rest
    name, colon, tag = path.rpartition(":") if ":" in path.rsplit("/", 1)[-1] else (path, "", "")
    if colon and not _TAG.match(tag):
        raise RegistryError(f"`{reference}`: `{tag}` is not a valid tag")
    if registry == DOCKER_HUB and "/" not in name:
        name = f"library/{name}"
    if not _REPOSITORY.match(name):
        raise RegistryError(f"`{reference}`: `{name}` is not a valid repository name")
    return ImageRef(
        registry=registry, repository=name, tag=tag or (None if digest else "latest"), digest=digest or None
    )


def under_prefix(ref: ImageRef, prefixes: list[str]) -> bool:
    """Whether the image's name sits under one of `prefixes`, matched by path segment:
    `ghcr.io/acme` covers `ghcr.io/acme/triager`, never `ghcr.io/acme-evil/triager`."""
    for prefix in prefixes:
        bare = prefix.strip().rstrip("/")
        if bare and (ref.name == bare or ref.name.startswith(bare + "/")):
            return True
    return False


class OciRegistryClient:
    """`RegistryClient` over the OCI distribution API, anonymous (a public image, or a registry
    whose token endpoint hands anonymous pull tokens)."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, timeout_s: float = 15.0) -> None:
        self._transport = transport
        self._timeout_s = timeout_s

    async def resolve(self, reference: str) -> str:
        ref = parse_ref(reference)
        host = _DOCKER_HUB_API if ref.registry == DOCKER_HUB else ref.registry
        scheme = "http" if host.startswith(("localhost", "127.0.0.1")) else "https"
        url = f"{scheme}://{host}/v2/{ref.repository}/manifests/{ref.digest or ref.tag}"
        async with httpx.AsyncClient(
            transport=self._transport, timeout=self._timeout_s, follow_redirects=False
        ) as client:
            response = await client.head(url, headers={"Accept": MANIFEST_TYPES})
            if response.status_code == 401:
                token = await _token(client, response.headers.get("www-authenticate", ""))
                response = await client.head(
                    url, headers={"Accept": MANIFEST_TYPES, "Authorization": f"Bearer {token}"}
                )
            if response.status_code == 404:
                raise RegistryError(f"`{reference}`: the registry has no such image")
            if response.status_code in (401, 403):
                raise RegistryError(f"`{reference}`: the registry refuses an anonymous pull")
            if response.status_code != 200:
                raise RegistryError(f"`{reference}`: the registry answered {response.status_code}")
            digest: str = str(response.headers.get("docker-content-digest", ""))
            if not digest:
                body = await client.get(url, headers=dict(response.request.headers))
                if body.status_code != 200 or len(body.content) > _MAX_MANIFEST_BYTES:
                    raise RegistryError(f"`{reference}`: the manifest cannot be read")
                digest = "sha256:" + hashlib.sha256(body.content).hexdigest()
        if not _DIGEST.match(digest):
            raise RegistryError(f"`{reference}`: the registry answered a digest that is not sha256")
        if ref.digest is not None and digest != ref.digest:
            raise RegistryError(f"`{reference}`: the registry serves {digest} under that digest")
        return digest

    async def pull_artifact(self, reference: str, media_type: str, max_bytes: int) -> tuple[str, bytes]:
        """An OCI artifact's single layer of `media_type` (a plugin, a plugin index; ADR 0093):
        `(manifest digest, layer bytes)`. The manifest is read by its digest or tag, its digest
        computed from its bytes; the layer is downloaded (a registry's redirect to its blob storage
        is followed, httpx drops the token on another origin), capped at `max_bytes`, and its sha256
        checked against the manifest's."""
        ref = parse_ref(reference)
        host = _DOCKER_HUB_API if ref.registry == DOCKER_HUB else ref.registry
        scheme = "http" if host.startswith(("localhost", "127.0.0.1")) else "https"
        base = f"{scheme}://{host}/v2/{ref.repository}"
        accept = {"Accept": "application/vnd.oci.image.manifest.v1+json"}
        async with httpx.AsyncClient(
            transport=self._transport, timeout=self._timeout_s, follow_redirects=False
        ) as client:
            url = f"{base}/manifests/{ref.digest or ref.tag}"
            response = await client.get(url, headers=accept)
            auth: dict[str, str] = {}
            if response.status_code == 401:
                token = await _token(client, response.headers.get("www-authenticate", ""))
                auth = {"Authorization": f"Bearer {token}"}
                response = await client.get(url, headers={**accept, **auth})
            if response.status_code == 404:
                raise RegistryError(f"`{reference}`: the registry has no such artifact")
            if response.status_code != 200:
                raise RegistryError(f"`{reference}`: the registry answered {response.status_code}")
            if len(response.content) > _MAX_MANIFEST_BYTES:
                raise RegistryError(f"`{reference}`: the manifest is too large")
            digest = "sha256:" + hashlib.sha256(response.content).hexdigest()
            announced = response.headers.get("docker-content-digest")
            if (announced and announced != digest) or (ref.digest is not None and ref.digest != digest):
                raise RegistryError(f"`{reference}`: the manifest's bytes do not match its digest")
            layer = _single_layer(reference, response, media_type)
            size = layer.get("size", 0)
            if not isinstance(size, int) or size > max_bytes:
                raise RegistryError(f"`{reference}`: the artifact is larger than {max_bytes} bytes")
            blob = await _blob(client, f"{base}/blobs/{layer['digest']}", auth, max_bytes, reference)
        if "sha256:" + hashlib.sha256(blob).hexdigest() != layer["digest"]:
            raise RegistryError(f"`{reference}`: the layer's bytes do not match the manifest's digest")
        return digest, blob


def _single_layer(reference: str, response: httpx.Response, media_type: str) -> dict[str, object]:
    try:
        manifest = response.json()
    except ValueError as exc:
        raise RegistryError(f"`{reference}`: the manifest is not JSON") from exc
    layers = manifest.get("layers") if isinstance(manifest, dict) else None
    found = [
        layer
        for layer in (layers if isinstance(layers, list) else [])
        if isinstance(layer, dict) and layer.get("mediaType") == media_type
    ]
    if len(found) != 1 or not _DIGEST.match(str(found[0].get("digest", ""))):
        raise RegistryError(f"`{reference}`: the artifact holds no single `{media_type}` layer")
    return found[0]


async def _blob(
    client: httpx.AsyncClient, url: str, auth: dict[str, str], max_bytes: int, reference: str
) -> bytes:
    chunks: list[bytes] = []
    received = 0
    async with client.stream("GET", url, headers=auth, follow_redirects=True) as response:
        if response.status_code != 200:
            raise RegistryError(f"`{reference}`: the registry answered {response.status_code} for the layer")
        async for chunk in response.aiter_bytes():
            received += len(chunk)
            if received > max_bytes:
                raise RegistryError(f"`{reference}`: the artifact is larger than {max_bytes} bytes")
            chunks.append(chunk)
    return b"".join(chunks)


_CHALLENGE = re.compile(r'(\w+)="([^"]*)"')


async def _token(client: httpx.AsyncClient, challenge: str) -> str:
    """The anonymous bearer token a `WWW-Authenticate: Bearer realm=…,service=…,scope=…` names."""
    scheme, _, params = challenge.partition(" ")
    fields = dict(_CHALLENGE.findall(params))
    realm = fields.pop("realm", "")
    if scheme.lower() != "bearer" or not realm.startswith("https://"):
        raise RegistryError("the registry asks for credentials the platform does not hold")
    answer = await client.get(realm, params=fields)
    if answer.status_code != 200:
        raise RegistryError(f"the registry's token endpoint answered {answer.status_code}")
    payload = answer.json()
    token = payload.get("token") or payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise RegistryError("the registry's token endpoint answered no token")
    return token
