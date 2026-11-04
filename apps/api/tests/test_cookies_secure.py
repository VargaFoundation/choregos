"""The session and OIDC cookies are `Secure` whenever the deployment is served over HTTPS.

A dev tenant behind a TLS-terminating ingress (`https://agentic.dev.diametral.com`) has
`env=dev`: the cookies used to be `Secure` in staging/prod only, so its session cookie would have
travelled in clear on any http hop. The URL says what the transport is, the environment does not.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from .conftest import login


def _flags(response_headers: list[str], name: str) -> str:
    lines = [h for h in response_headers if h.startswith(f"{name}=")]
    assert len(lines) == 1, f"expected one Set-Cookie for {name}, got {response_headers}"
    return lines[0].lower()


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    from choregos_api.config import reset_settings_cache

    for key, value in env.items():
        monkeypatch.setenv(f"CHOREGOS_{key.upper()}", value)
    reset_settings_cache()


@pytest.mark.parametrize(
    ("env", "public_url", "api_url", "secure"),
    [
        ("dev", "http://localhost:3000", "http://localhost:8000", False),
        ("dev", "https://agentic.dev.example.test", "https://agentic.dev.example.test", True),
        ("dev", "http://front.test", "HTTPS://api.example.test", True),
        ("dev", "https://front.example.test", "http://api.test", True),
        ("staging", "http://front.test", "http://api.test", True),
        ("prod", "http://front.test", "http://api.test", True),
    ],
)
def test_cookies_secure_follows_the_url_and_the_environment(
    monkeypatch: pytest.MonkeyPatch, env: str, public_url: str, api_url: str, secure: bool
) -> None:
    from choregos_api.config import Settings

    settings_obj = Settings(env=env, public_url=public_url, api_url=api_url, dev_login_enabled=False)  # type: ignore[arg-type]
    assert settings_obj.cookies_secure is secure


async def test_over_https_in_dev_both_cookies_are_secure(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settings(
        monkeypatch,
        public_url="https://agentic.dev.example.test",
        api_url="https://agentic.dev.example.test",
    )
    # a browser returns a Secure cookie over https only
    client.base_url = "https://agentic.dev.example.test"
    start = await client.get("/api/v1/auth/login", params={"as": "admin@varga.dev"})
    assert start.status_code == 307, start.text
    assert "secure" in _flags(start.headers.get_list("set-cookie"), "choregos_oidc")
    end = await client.get(start.headers["location"])
    assert end.status_code == 307, end.text
    assert "secure" in _flags(end.headers.get_list("set-cookie"), "choregos_session")


async def test_over_http_in_dev_cookies_stay_usable_on_a_bench(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settings(monkeypatch, public_url="http://localhost:3000", api_url="http://localhost:8000")
    start = await client.get("/api/v1/auth/login", params={"as": "admin@varga.dev"})
    assert "secure" not in _flags(start.headers.get_list("set-cookie"), "choregos_oidc")
    end = await client.get(start.headers["location"])
    assert "secure" not in _flags(end.headers.get_list("set-cookie"), "choregos_session")
    await login(client, "admin@varga.dev")
