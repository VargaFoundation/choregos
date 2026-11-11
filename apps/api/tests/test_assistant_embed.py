"""The embedded component's server side (S63-12, ADR 0139 point 6).

`config.embed_origins` says who may frame the console's embed page; it is validated as plain origins (no
wildcard), it is kept even when the manifest's `config_schema` is closed, and it is served to the console
by a public route that carries origins and nothing else. The embed page's silent sign-in is OIDC
`prompt=none`: an IdP that cannot answer silently sends the browser back to the page with `signin=required`
(and any other IdP error is refused, not swallowed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from .kits.assistant_install import ORG, _install, _plugin, offline  # noqa: F401

EMBED = "/api/v1/public/assistants/varga/investment-assistant/embed"
ORIGINS = ["https://app.varga.dev", "http://localhost:3100"]


async def _installed(client: AsyncClient, **config: Any) -> None:
    await _plugin(client)
    installed = await _install(client, config={"house_name": "Varga", **config})
    assert installed.status_code == 201, installed.text


async def test_the_public_route_serves_the_installations_origins_to_anyone(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
) -> None:
    await _installed(client, embed_origins=[*ORIGINS, "https://APP.varga.dev:443", "https://app.varga.dev/"])
    stranger = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")  # no session at all
    response = await stranger.get(EMBED)
    assert response.status_code == 200, response.text
    assert response.json() == {"embed_origins": ORIGINS}, "normalised, de-duplicated, nothing else"
    missing = await stranger.get("/api/v1/public/assistants/varga/nobody/embed")
    assert missing.status_code == 404
    assert (
        await stranger.get("/api/v1/public/assistants/nowhere/investment-assistant/embed")
    ).status_code == 404


async def test_an_installation_without_origins_is_framed_by_nobody(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
) -> None:
    await _installed(client)
    assert (await client.get(EMBED)).json() == {"embed_origins": []}


@pytest.mark.parametrize(
    "bad",
    [
        ["*"],
        ["https://*.varga.dev"],
        ["https://app.varga.dev/path"],
        ["http://app.varga.dev"],
        "https://x.dev",
        [3],
    ],
)
async def test_embed_origins_must_be_plain_origins(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
    bad: Any,
) -> None:
    await _installed(client)
    refused = await _install(client, slug="second", config={"house_name": "Varga", "embed_origins": bad})
    assert refused.status_code == 422, refused.text
    assert "embed_origins" in refused.text
    changed = await client.patch(
        f"{ORG}/assistants/investment-assistant", json={"config": {"house_name": "V", "embed_origins": bad}}
    )
    assert changed.status_code == 422, changed.text
    assert (await client.get(EMBED)).json() == {"embed_origins": []}, "a refused value changes nothing"


async def test_origins_can_be_changed_later(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
) -> None:
    await _installed(client, embed_origins=["https://app.varga.dev"])
    changed = await client.patch(
        f"{ORG}/assistants/investment-assistant",
        json={"config": {"house_name": "Varga", "embed_origins": ["https://intranet.varga.dev"]}},
    )
    assert changed.status_code == 200, changed.text
    assert (await client.get(EMBED)).json() == {"embed_origins": ["https://intranet.varga.dev"]}


ISSUER = "https://idp.example.test/realms/x"


@pytest.fixture
def idp(org: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    from choregos_api.config import reset_settings_cache
    from choregos_api.routers.auth import forget_discovery

    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", ISSUER)
    reset_settings_cache()
    forget_discovery()
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            return_value=Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "userinfo_endpoint": f"{ISSUER}/userinfo",
                },
            )
        )
        yield mock
    reset_settings_cache()
    forget_discovery()


async def _silent(client: AsyncClient) -> dict[str, list[str]]:
    start = await client.get(
        "/api/v1/auth/login",
        params={"silent": "1", "redirect_to": "/embed/assistants/varga/coach?context=e30"},
    )
    assert start.status_code == 307, start.text
    return parse_qs(urlparse(start.headers["location"]).query)


async def test_the_silent_sign_in_asks_the_idp_for_prompt_none(client: AsyncClient, idp: Any) -> None:
    assert (await _silent(client))["prompt"] == ["none"]
    plain = await client.get("/api/v1/auth/login")
    assert "prompt" not in parse_qs(urlparse(plain.headers["location"]).query)


@pytest.mark.parametrize("error", ["login_required", "interaction_required", "consent_required"])
async def test_an_idp_that_cannot_answer_silently_sends_the_browser_back_with_signin_required(
    client: AsyncClient, idp: Any, error: str
) -> None:
    parameters = await _silent(client)
    back = await client.get("/api/v1/auth/callback", params={"error": error, "state": parameters["state"][0]})
    assert back.status_code == 307, back.text
    assert back.headers["location"] == "/embed/assistants/varga/coach?context=e30&signin=required"
    assert not client.cookies.get("choregos_session"), "no session was opened"


async def test_any_other_idp_error_is_refused_and_a_forged_state_is_too(
    client: AsyncClient, idp: Any
) -> None:
    parameters = await _silent(client)
    refused = await client.get(
        "/api/v1/auth/callback", params={"error": "access_denied", "state": parameters["state"][0]}
    )
    assert refused.status_code == 401, refused.text
    forged = await client.get("/api/v1/auth/callback", params={"error": "login_required", "state": "forged"})
    assert forged.status_code == 401, "the redirect is only for a sign-in this browser started"
