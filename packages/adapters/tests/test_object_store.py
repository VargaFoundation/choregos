"""The object store (ADR 0049): the S3 adapter signs like AWS and the fake behaves like it."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from choregos_adapters.fakes import FakeObjectStore
from choregos_adapters.storage import (
    ObjectNotFoundError,
    ObjectStore,
    ObjectStoreError,
    S3ObjectStore,
    sigv4_headers,
)


def test_the_signature_matches_the_aws_reference_example() -> None:
    """The "GET Object" example of the AWS SigV4 documentation, header for header."""
    headers = sigv4_headers(
        method="GET",
        host="examplebucket.s3.amazonaws.com",
        path="/test.txt",
        headers={"Range": "bytes=0-9"},
        payload_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        now=datetime(2013, 5, 24, tzinfo=UTC),
    )
    assert headers["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


async def test_s3_puts_and_gets_path_style_with_a_signed_request() -> None:
    seen: list[httpx.Request] = []
    stored: dict[str, bytes] = {}

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PUT":
            stored[request.url.path] = request.content
            return httpx.Response(200)
        if request.url.path in stored:
            return httpx.Response(200, content=stored[request.url.path])
        return httpx.Response(404)

    store = S3ObjectStore(
        "s3://choregos/artifacts",
        endpoint="http://localhost:9000",
        access_key="choregos",
        secret_key="secret",
        client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
    )
    url = await store.put("orgs/acme/deck", b"PK\x03\x04", "application/octet-stream")
    assert url == "s3://choregos/artifacts/orgs/acme/deck"
    assert seen[0].url.path == "/choregos/artifacts/orgs/acme/deck"
    assert seen[0].headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=choregos/")
    assert "content-type" in seen[0].headers["Authorization"]
    assert seen[0].headers["x-amz-content-sha256"] != "UNSIGNED-PAYLOAD"
    assert await store.get("orgs/acme/deck") == b"PK\x03\x04"
    with pytest.raises(ObjectNotFoundError):
        await store.get("orgs/acme/absent")


async def test_s3_says_what_it_refused() -> None:
    store = S3ObjectStore(
        "s3://choregos",
        endpoint="http://localhost:9000",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403, text="denied"))),
    )
    with pytest.raises(ObjectStoreError, match="403 denied"):
        await store.put("k", b"x", "text/plain")


def test_an_s3_url_names_a_bucket() -> None:
    with pytest.raises(ValueError, match="s3://"):
        S3ObjectStore("memory://x")
    with pytest.raises(ValueError, match="no bucket"):
        S3ObjectStore("s3://")
    assert S3ObjectStore("s3://b", region="eu-west-3").endpoint == "https://s3.eu-west-3.amazonaws.com"


async def test_the_fake_is_an_object_store_and_can_be_tampered_with() -> None:
    store = FakeObjectStore()
    assert isinstance(store, ObjectStore)
    assert isinstance(S3ObjectStore("s3://b"), ObjectStore)
    assert await store.put("a/b", b"one", "text/plain") == "memory://choregos/a/b"
    store.tamper("a/b", b"two")
    assert await store.get("a/b") == b"two"
    store.fail_next()
    with pytest.raises(ObjectStoreError):
        await store.get("a/b")
    store.forget("a/b")
    with pytest.raises(ObjectNotFoundError):
        await store.get("a/b")


async def test_s3_check_is_a_signed_head_on_the_bucket_and_says_why_it_fails() -> None:
    seen: list[httpx.Request] = []
    status = {"code": 200}

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status["code"], text="<Error>SECRET-BODY</Error>")

    store = S3ObjectStore(
        "s3://choregos/artifacts",
        endpoint="http://localhost:9000",
        access_key="AKEY",
        secret_key="SKEY",
        client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
    )
    await store.check()
    assert [(r.method, r.url.path) for r in seen] == [("HEAD", "/choregos")], "the bucket, nothing written"
    assert "Authorization" in seen[0].headers
    for code, words in ((403, "refused the credentials"), (404, "does not exist"), (500, "answered 500")):
        status["code"] = code
        with pytest.raises(ObjectStoreError, match=words) as raised:
            await store.check()
        assert "SECRET-BODY" not in str(raised.value) and "SKEY" not in str(raised.value)


async def test_s3_check_without_keys_says_so_and_an_unreachable_store_is_an_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    anonymous = S3ObjectStore(
        "s3://choregos", client=httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    )
    with pytest.raises(ObjectStoreError, match="no access key is set"):
        await anonymous.check()

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    unreachable = S3ObjectStore(
        "s3://choregos", client=httpx.AsyncClient(transport=httpx.MockTransport(down))
    )
    with pytest.raises(ObjectStoreError, match="unreachable"):
        await unreachable.check()


async def test_the_fake_check_answers_and_can_be_scripted_to_fail() -> None:
    store = FakeObjectStore()
    await store.check()
    store.fail_next("down")
    with pytest.raises(ObjectStoreError, match="down"):
        await store.check()
