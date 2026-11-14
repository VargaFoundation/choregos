"""A person's photo (S66-42, ADR 0162): uploaded by them, re-encoded, served same-origin to whoever shares an
organisation with them, and to nobody else."""

from __future__ import annotations

import io

import pytest
from httpx import AsyncClient
from PIL import Image

from .conftest import login

ORG = "/api/v1/orgs/varga"


def _image(fmt: str, size: tuple[int, int] = (64, 48), **save: object) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(out, format=fmt, **save)
    return out.getvalue()


async def _put(
    client: AsyncClient, data: bytes, content_type: str = "image/png"
) -> tuple[int, dict[str, object]]:
    response = await client.put("/api/v1/me/avatar", files={"file": ("me", data, content_type)})
    body = (
        response.json()
        if response.content and response.headers.get("content-type", "").startswith("application/")
        else {}
    )
    return response.status_code, body


async def _member(client: AsyncClient, email: str, role: str = "developer") -> str:
    added = await client.post(f"{ORG}/members", json={"email": email, "role": role})
    assert added.status_code == 201, added.text
    return str(added.json()["user_id"])


async def test_a_photo_is_re_encoded_and_served_same_origin(client: AsyncClient, admin: str) -> None:
    jpeg = _image("JPEG", (900, 600), exif=b"Exif\x00\x00MM\x00*\x00\x00\x00\x08\x00\x00")
    status, body = await _put(client, jpeg, "image/jpeg")
    assert status == 200, body
    url = str(body["avatar_url"])
    me = (await client.get("/api/v1/me")).json()
    assert url.startswith(f"/api/v1/users/{me['id']}/avatar?v=") and me["avatar_url"] == url

    served = await client.get(url)
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/webp"
    assert served.headers["x-content-type-options"] == "nosniff"
    stored = Image.open(io.BytesIO(served.content))
    assert stored.format == "WEBP" and stored.size == (512, 512), "a square of at most 512 pixels"
    assert not stored.getexif(), "no metadata survives the re-encoding"
    again = await client.get(url, headers={"If-None-Match": served.headers["etag"]})
    assert again.status_code == 304

    assert (await client.delete("/api/v1/me/avatar")).status_code == 204
    assert (await client.get(url)).status_code == 404
    assert (await client.get("/api/v1/me")).json()["avatar_url"] is None


async def test_a_photo_that_is_not_a_bounded_png_jpeg_or_webp_is_refused(
    client: AsyncClient, admin: str
) -> None:
    assert (await _put(client, _image("GIF"), "image/gif"))[0] == 415
    assert (await _put(client, b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"))[0] == 415
    # announced as a PNG by its name and type, it is a GIF by its bytes
    assert (await _put(client, _image("GIF"), "image/png"))[0] == 415
    assert (await _put(client, b"\x89PNG\r\n\x1a\n" + b"\x00" * 64))[0] == 422, "a PNG that does not decode"
    assert (await _put(client, _image("PNG", (16, 16))))[0] == 422, "too small"
    assert (await _put(client, _image("PNG", (5000, 40))))[0] == 422, "too large a side"
    big = b"\x89PNG\r\n\x1a\n" + bytes(2 * 1024 * 1024)
    assert (await _put(client, big))[0] == 413
    assert (await client.get("/api/v1/me")).json()["avatar_url"] is None, "nothing was stored"
    assert (await _put(client, _image("WEBP")))[0] == 200


async def test_the_photo_is_read_by_a_shared_organisation_only(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    await _member(client, "dev@varga.dev")
    status, body = await _put(client, _image("PNG"))
    assert status == 200
    url = str(body["avatar_url"])

    await login(client, "dev@varga.dev")
    assert (await client.get(url)).status_code == 200, "a member of the same organisation sees it"

    # The development sign-in lands a new person in every organisation unless a default one is set.
    async with session_scope() as session:
        session.add(Organization(slug="other", name="Other"))
    monkeypatch.setenv("CHOREGOS_OIDC_DEFAULT_ORG", "other")
    monkeypatch.setenv("CHOREGOS_DEV_ADMIN_EMAILS", "eve@other.dev")
    reset_settings_cache()
    await login(client, "eve@other.dev")
    assert (await client.get(url)).status_code == 404, "another organisation's administrator does not"
