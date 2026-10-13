# SPDX-License-Identifier: Apache-2.0
"""The object store: where the platform keeps the bytes of file artifacts (ADR 0049).

Only the API writes and reads it. A runner never holds object store credentials: it hands its
files to the internal API, which hashes them, stores them and records them.

Two implementations: `S3ObjectStore`, which speaks the S3 protocol (AWS, RustFS, MinIO, Ceph) with
a hand-written SigV4 signature over `httpx` — no SDK in the core — and `FakeObjectStore`
(`choregos_adapters.fakes.storage`), in memory, scriptable, for `CHOREGOS_FAKES=1` and tests.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from urllib.parse import quote

import httpx

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class ObjectStoreError(RuntimeError):
    """The object store refused or could not be reached."""


class ObjectNotFoundError(ObjectStoreError):
    """No object under this key."""


@runtime_checkable
class ObjectStore(Protocol):
    """Content in, content out, by key. Keys are relative to the store's bucket and prefix."""

    def url_of(self, key: str) -> str:
        """The stable address of a key, recorded with the artifact (`s3://bucket/prefix/key`)."""
        ...

    async def put(self, key: str, data: bytes, media_type: str) -> str:
        """Writes the object (overwriting is harmless: keys are content-addressed); returns its URL."""
        ...

    async def get(self, key: str) -> bytes:
        """Reads the object; `ObjectNotFoundError` when it is absent."""
        ...


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def sigv4_headers(  # noqa: PLR0913 - the inputs of the signature, keyword-only
    *,
    method: str,
    host: str,
    path: str,
    query: str = "",
    headers: dict[str, str] | None = None,
    payload_sha256: str,
    access_key: str,
    secret_key: str,
    region: str,
    now: datetime,
    service: str = "s3",
) -> dict[str, str]:
    """The headers of an AWS Signature Version 4 request: `x-amz-date`, `x-amz-content-sha256`
    and `Authorization`. `path` is already URI-encoded; every header passed is signed."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = now.strftime("%Y%m%d")
    signed = {k.lower(): v.strip() for k, v in (headers or {}).items()}
    signed.update({"host": host, "x-amz-date": amz_date, "x-amz-content-sha256": payload_sha256})
    names = sorted(signed)
    canonical_headers = "".join(f"{name}:{signed[name]}\n" for name in names)
    signed_headers = ";".join(names)
    canonical_request = "\n".join([method, path, query, canonical_headers, signed_headers, payload_sha256])
    scope = f"{day}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret_key}".encode(), day), region), service), "aws4_request")
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_sha256,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }


class S3ObjectStore:
    """An S3 bucket, path-style addressed (`<endpoint>/<bucket>/<key>`), which every S3-compatible
    server accepts — RustFS in development included."""

    def __init__(
        self,
        url: str,
        *,
        endpoint: str = "",
        region: str = "us-east-1",
        access_key: str = "",
        secret_key: str = "",
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        if not url.startswith("s3://"):
            raise ValueError(f"an S3 object store URL starts with s3://, not `{url}`")
        bucket, _, prefix = url.removeprefix("s3://").partition("/")
        if not bucket:
            raise ValueError(f"no bucket in `{url}`")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.endpoint = (endpoint or f"https://s3.{region}.amazonaws.com").rstrip("/")
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self._client = client
        self._timeout = timeout

    def _full_key(self, key: str) -> str:
        key = key.lstrip("/")
        return f"{self.prefix}/{key}" if self.prefix else key

    def url_of(self, key: str) -> str:
        return f"s3://{self.bucket}/{self._full_key(key)}"

    async def _request(
        self, method: str, key: str, data: bytes = b"", media_type: str = ""
    ) -> httpx.Response:
        base = httpx.URL(self.endpoint)
        base_path = base.path.rstrip("/")
        path = quote(f"{base_path}/{self.bucket}/{self._full_key(key)}", safe="/-_.~")
        host = base.host + (f":{base.port}" if base.port else "")
        payload = hashlib.sha256(data).hexdigest() if data else EMPTY_SHA256
        extra = {"content-type": media_type} if media_type else {}
        headers = {**extra}
        if self.access_key:
            headers.update(
                sigv4_headers(
                    method=method,
                    host=host,
                    path=path,
                    headers=extra,
                    payload_sha256=payload,
                    access_key=self.access_key,
                    secret_key=self.secret_key,
                    region=self.region,
                    now=datetime.now(UTC),
                )
            )
        url = f"{base.scheme}://{host}{path}"
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            return await client.request(method, url, content=data or None, headers=headers)
        except httpx.HTTPError as exc:
            raise ObjectStoreError(f"object store unreachable ({self.endpoint}): {exc}") from exc
        finally:
            if self._client is None:
                await client.aclose()

    async def put(self, key: str, data: bytes, media_type: str) -> str:
        response = await self._request("PUT", key, data, media_type)
        if response.status_code >= 300:
            raise ObjectStoreError(
                f"object store refused PUT {self.url_of(key)}: {response.status_code} {response.text[:300]}"
            )
        return self.url_of(key)

    async def get(self, key: str) -> bytes:
        response = await self._request("GET", key)
        if response.status_code == 404:
            raise ObjectNotFoundError(f"no object at {self.url_of(key)}")
        if response.status_code >= 300:
            raise ObjectStoreError(
                f"object store refused GET {self.url_of(key)}: {response.status_code} {response.text[:300]}"
            )
        return response.content


__all__ = [
    "ObjectNotFoundError",
    "ObjectStore",
    "ObjectStoreError",
    "S3ObjectStore",
    "sigv4_headers",
]
