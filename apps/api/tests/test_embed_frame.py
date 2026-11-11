"""The embedded component's session in a cross-site frame (S63-12): the frame token (`embed_frame`).

A page framed by another site does not carry the console's `SameSite=Lax` cookie. The popup of the sign-in
mints a token; the frame sends it as `X-Choregos-Embed`. It opens the component's routes and nothing else."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from choregos_api import embed_frame
from choregos_api.config import get_settings
from choregos_api.security import sign_session
from httpx import ASGITransport, AsyncClient

from .conftest import login
from .kits.assistant_install import (
    MARKET,
    NEWS,
    _bind,
    _connector,
    _install,
    _member,
    _plugin,
    offline,  # noqa: F401
)

SLUG = "investment-assistant"
ME = "/api/v1/me"


async def _ready_and_token(client: AsyncClient, app: Any) -> tuple[AsyncClient, str]:
    await _member(client, "reader@varga.dev", ["investors"])
    await _plugin(client)
    await _install(client, audience_groups=["investors"])
    await _connector("news-feed", NEWS)
    await _connector("markets", MARKET)
    await _bind(client, "news", "news-feed")
    await _bind(client, "market_data", "markets")
    reader = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(reader, "reader@varga.dev")
    minted = await reader.post(f"{ME}/assistants/varga/{SLUG}/embed-token")
    assert minted.status_code == 200, minted.text
    assert minted.json()["expires_in"] == embed_frame.TTL_S
    return reader, str(minted.json()["token"])


def _frame(app: Any, token: str) -> AsyncClient:
    """A browser frame: NO session cookie (it is `Lax`, the host is another site), the token only."""
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={embed_frame.HEADER: token}
    )


async def test_a_frame_with_the_token_alone_converses_and_nothing_more(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
) -> None:
    _, token = await _ready_and_token(client, app)
    frame = _frame(app, token)
    # Without the token a frame has no session: the cookie is not there.
    bare = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    assert (await bare.get(ME)).status_code == 401
    assert (await frame.get(ME)).status_code == 200

    opened = await frame.post(f"{ME}/assistants/varga/{SLUG}/conversations", json={})
    assert opened.status_code == 201, opened.text
    conversation = opened.json()["id"]
    assert (await frame.get(f"{ME}/conversations/{conversation}")).status_code == 200
    assert (await frame.get(f"{ME}/conversations/{conversation}/actions")).status_code == 200

    # What the component never calls is refused, whoever holds the token.
    assert (await frame.get(f"{ME}/assistants")).status_code == 403
    assert (await frame.get(f"{ME}/conversations")).status_code == 403
    assert (await frame.delete(f"{ME}/conversations/{conversation}")).status_code == 403
    assert (await frame.post(f"{ME}/assistants/varga/{SLUG}/embed-token")).status_code == 403
    assert (await frame.get("/api/v1/orgs/varga/assistants")).status_code == 403


async def test_a_bad_expired_or_foreign_token_opens_nothing(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
) -> None:
    reader, token = await _ready_and_token(client, app)
    assert (await _frame(app, token + "x").get(ME)).status_code == 401
    assert (await _frame(app, "nonsense").get(ME)).status_code == 401
    settings = get_settings()
    claims = {"sub": "u", "org": "varga", "slug": SLUG, "inst": "i", "exp": int(time.time()) - 5}
    expired = sign_session(claims, settings, purpose=embed_frame.PURPOSE)
    assert (await _frame(app, expired).get(ME)).status_code == 401
    # A session cookie is not a frame token (another purpose never reads as this one).
    cookie_value = reader.cookies.get(settings.session_cookie) or ""
    assert (await _frame(app, cookie_value).get(ME)).status_code == 401
    # A person outside the audience is not given one: the assistant does not exist for them.
    await _member(client, "outsider@varga.dev", ["nobody"])
    outsider = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(outsider, "outsider@varga.dev")
    assert (await outsider.post(f"{ME}/assistants/varga/{SLUG}/embed-token")).status_code == 404


async def test_a_token_ends_with_the_browser_session(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
) -> None:
    reader, token = await _ready_and_token(client, app)
    frame = _frame(app, token)
    assert (await frame.get(ME)).status_code == 200
    assert (await reader.post("/api/v1/auth/logout")).status_code in (200, 204, 307)
    assert (await frame.get(ME)).status_code == 401


def test_a_token_names_one_installation_and_a_few_routes() -> None:
    token = embed_frame.FrameToken("u", "s", "varga", SLUG, "i1", 0)
    allowed = embed_frame.route_of
    assert allowed("POST", f"/api/v1/me/assistants/varga/{SLUG}/conversations", token) == (True, None)
    assert allowed("POST", f"/api/v1/me/assistants/acme/{SLUG}/conversations", token) == (False, None)
    assert allowed("POST", "/api/v1/me/assistants/varga/other/conversations", token) == (False, None)
    assert allowed("POST", "/api/v1/me/conversations/c1/messages", token) == (True, "c1")
    assert allowed("POST", "/api/v1/me/conversations/c1/actions/a1/confirm", token) == (True, "c1")
    assert allowed("GET", f"/api/v1/me/assistants/varga/{SLUG}/memory/notes", token) == (True, None)
    assert allowed("DELETE", "/api/v1/me/conversations/c1", token) == (False, "c1")
    assert allowed("GET", "/api/v1/me/inbox", token) == (False, None)
