"""The session secret: what the API refuses at start, and what a signed value is bound to (S47-01).

The key signs the session cookie and the OIDC and remote-OAuth handshakes. It used to be the
published default `dev-session-secret-change-me` whatever the environment, possibly empty, and the
tag it produced was 128 bits long and bound to nothing: an OIDC `state` (which anyone who starts a
sign-in obtains) was as good as a session cookie for `read_session`.

The refusal belongs to `create_app()`, not to a `Settings` validator: the orchestrator workers, the
migrations Job and the trace-drift CronJob build the same `Settings` without the secret and never
sign a session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from choregos_adapters.fakes.a2a import FakeA2aAgent
from httpx import AsyncClient

from .conftest_remote import remote_agent, set_sealing_key

LONG_SECRET = "s" * 48


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    """Each test reads the environment it sets, and leaves no cached `Settings` behind."""
    from choregos_api.config import reset_settings_cache

    reset_settings_cache()
    yield
    reset_settings_cache()


def _env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    """A deployed-looking environment without a session secret, plus `env` (CHOREGOS_ names)."""
    from choregos_api.config import reset_settings_cache

    monkeypatch.delenv("CHOREGOS_SESSION_SECRET", raising=False)
    # The dev sign-in is refused in staging/prod: the suites turn it on, a prod app needs it off.
    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    reset_settings_cache()


@pytest.mark.parametrize(
    "env",
    [
        {"CHOREGOS_ENV": "prod"},
        {"CHOREGOS_ENV": "prod", "CHOREGOS_SESSION_SECRET": "short"},
        {"CHOREGOS_ENV": "dev", "CHOREGOS_PUBLIC_URL": "https://c.example"},
    ],
    ids=["prod-default-secret", "prod-short-secret", "dev-over-https-default-secret"],
)
def test_the_api_refuses_the_published_secret_when_deployed(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    from choregos_api.main import create_app

    _env(monkeypatch, **env)
    with pytest.raises(RuntimeError, match="CHOREGOS_SESSION_SECRET"):
        create_app()
    # a 48-byte secret starts the same deployment
    monkeypatch.setenv("CHOREGOS_SESSION_SECRET", LONG_SECRET)
    from choregos_api.config import reset_settings_cache

    reset_settings_cache()
    assert create_app() is not None


def test_the_error_says_what_to_do(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.main import create_app

    _env(monkeypatch, CHOREGOS_ENV="staging", CHOREGOS_SESSION_SECRET="x" * 31)
    with pytest.raises(RuntimeError, match=r"32 bytes.*openssl rand -base64 48"):
        create_app()


def test_the_limit_is_32_bytes_not_32_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import Settings
    from choregos_api.security import check_session_secret

    _env(monkeypatch)
    deployed: dict[str, Any] = {"env": "prod", "dev_login_enabled": False}
    check_session_secret(Settings(session_secret="x" * 32, **deployed))
    with pytest.raises(RuntimeError, match="CHOREGOS_SESSION_SECRET"):
        check_session_secret(Settings(session_secret="x" * 31, **deployed))
    # 16 characters, 32 bytes: counted in bytes
    check_session_secret(Settings(session_secret="é" * 16, **deployed))
    with pytest.raises(RuntimeError, match="CHOREGOS_SESSION_SECRET"):
        check_session_secret(Settings(session_secret="é" * 15, **deployed))


def test_the_published_default_is_refused_by_value_not_only_by_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default is 28 bytes today; a longer one, published the same way, must stay refused."""
    from choregos_api import security
    from choregos_api.config import Settings

    _env(monkeypatch)
    published = "d" * 40
    monkeypatch.setattr(security, "DEFAULT_SESSION_SECRET", published)
    deployed = Settings(env="prod", dev_login_enabled=False, session_secret=published)
    with pytest.raises(RuntimeError, match="published default"):
        security.check_session_secret(deployed)


def test_a_development_instance_over_http_keeps_starting(monkeypatch: pytest.MonkeyPatch) -> None:
    """`docker compose up` (a 31-character secret, env dev, http) and the suites must not break."""
    from choregos_api.config import Settings
    from choregos_api.main import create_app
    from choregos_api.security import check_session_secret

    _env(monkeypatch, CHOREGOS_ENV="dev")
    assert create_app() is not None  # the published default, on a dev instance over http
    monkeypatch.setenv("CHOREGOS_SESSION_SECRET", "local-install-only-not-a-secret")
    from choregos_api.config import reset_settings_cache

    reset_settings_cache()
    assert create_app() is not None
    check_session_secret(Settings(env="test", session_secret="short", dev_login_enabled=True))


def test_settings_without_a_session_secret_still_build_for_the_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the orchestrator and the migrations Job construct: no secret, a deployed-looking env.

    Fails if the length rule is moved into a `Settings` validator: every worker would crash-loop
    and the migrations hook would fail every install.
    """
    from choregos_api.config import DEFAULT_SESSION_SECRET, Settings

    _env(monkeypatch)
    settings = Settings(
        env="prod", dev_login_enabled=False, public_url="https://app.x", api_url="https://api.x"
    )
    assert settings.deployed is True
    assert settings.session_secret == DEFAULT_SESSION_SECRET
    assert settings.session_secret_previous == ""


@pytest.mark.parametrize("empty", ["", "  "])
def test_an_empty_session_secret_stops_the_api_everywhere(
    monkeypatch: pytest.MonkeyPatch, empty: str
) -> None:
    from choregos_api.main import create_app

    _env(monkeypatch, CHOREGOS_ENV="test", CHOREGOS_SESSION_SECRET=empty)
    with pytest.raises(RuntimeError, match="CHOREGOS_SESSION_SECRET is empty"):
        create_app()


def test_a_short_previous_key_stops_a_deployed_api_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import Settings
    from choregos_api.security import check_session_secret

    _env(monkeypatch)
    settings: dict[str, Any] = {"session_secret": LONG_SECRET, "session_secret_previous": "old-and-short"}
    with pytest.raises(RuntimeError, match="CHOREGOS_SESSION_SECRET_PREVIOUS"):
        check_session_secret(Settings(env="prod", dev_login_enabled=False, **settings))
    check_session_secret(Settings(env="prod", dev_login_enabled=False, session_secret=LONG_SECRET))
    check_session_secret(
        Settings(
            env="prod", dev_login_enabled=False, session_secret_previous="p" * 32, session_secret=LONG_SECRET
        )
    )
    check_session_secret(Settings(env="dev", **settings))


def test_a_signed_value_carries_a_full_length_tag_bound_to_its_purpose() -> None:
    from choregos_api.security import read_session, sign_session

    payload = {"sub": "u", "exp": int(time.time()) + 60}
    cookie = sign_session(payload)
    assert re.fullmatch(r"[A-Za-z0-9_-]+\.[0-9a-f]{64}", cookie), cookie
    assert read_session(cookie) == payload

    state = sign_session(payload, purpose="oidc-state")
    assert read_session(state) is None
    assert read_session(state, purpose="oidc-handshake") is None
    assert read_session(state, purpose="oidc-state") == payload
    # and the other way round: a session cookie is no handshake
    assert read_session(cookie, purpose="oidc-state") is None


def test_an_oidc_state_is_not_a_session() -> None:
    from choregos_api.config import get_settings
    from choregos_api.routers import auth
    from choregos_api.security import read_session

    state, cookie, _, _ = auth._handshake(get_settings(), "/x")
    assert read_session(state) is None
    assert read_session(cookie) is None
    assert read_session(state, purpose="oidc-state") is not None
    assert read_session(state, purpose="oidc-state")["r"] == "/x"  # type: ignore[index]
    # the state is not the handshake cookie either, nor the other way round
    assert read_session(state, purpose="oidc-handshake") is None
    assert read_session(cookie, purpose="oidc-state") is None
    assert read_session(cookie, purpose="oidc-handshake") is not None


def test_a_remote_oauth_state_is_neither_a_session_nor_an_oidc_state() -> None:
    from choregos_api.security import read_session, sign_session

    payload = {"n": "nonce", "exp": int(time.time()) + 60}
    state = sign_session(payload, purpose="remote-oauth-state")
    handshake = sign_session(payload, purpose="remote-oauth-handshake")
    for purpose in ("session", "oidc-state", "oidc-handshake"):
        assert read_session(state, purpose=purpose) is None, purpose
        assert read_session(handshake, purpose=purpose) is None, purpose
    assert read_session(state, purpose="remote-oauth-handshake") is None
    assert read_session(handshake, purpose="remote-oauth-state") is None
    assert read_session(state, purpose="remote-oauth-state") == payload
    assert read_session(handshake, purpose="remote-oauth-handshake") == payload


def _legacy_cookie(secret: str, payload: dict[str, Any]) -> str:
    """A cookie built the way the previous release did: no purpose, a 128-bit tag."""
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{body}.{hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]}"


def test_a_cookie_signed_before_this_release_is_still_read() -> None:
    from choregos_api.config import get_settings
    from choregos_api.security import read_session

    payload = {"sub": "u", "exp": int(time.time()) + 60}
    legacy = _legacy_cookie(get_settings().session_secret, payload)
    assert read_session(legacy) == payload
    # the legacy tag is accepted for a session only, never for another purpose
    for purpose in ("oidc-state", "oidc-handshake", "remote-oauth-state", "remote-oauth-handshake"):
        assert read_session(legacy, purpose=purpose) is None, purpose


def test_a_legacy_cookie_needs_the_right_key_and_the_removal_switch_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_api import security
    from choregos_api.config import Settings

    payload = {"sub": "u", "exp": int(time.time()) + 60}
    other_key = "o" * 48
    legacy = _legacy_cookie(other_key, payload)
    assert security.read_session(legacy) is None
    # a 32-character signature that is not the tag of any key
    body = legacy.rsplit(".", 1)[0]
    assert security.read_session(f"{body}.{'0' * 32}") is None
    # the previous release's cookie reads with the previous key too (a rotation during the upgrade)
    rotating = Settings(session_secret=LONG_SECRET, session_secret_previous=other_key)
    assert security.read_session(legacy, rotating) == payload
    # the switch that ends the legacy path (issue 1040) turns the same cookie away
    monkeypatch.setattr(security, "ACCEPT_LEGACY_SESSION_TAGS", False)
    assert security.read_session(legacy, rotating) is None
    assert security.read_session(_legacy_cookie(security.get_settings().session_secret, payload)) is None


async def test_a_session_cookie_signed_before_this_release_still_signs_you_in(
    client: AsyncClient, admin: str
) -> None:
    """Through the real dependency: the same body, re-tagged the previous release's way."""
    from choregos_api.config import get_settings

    cookie = client.cookies.get("choregos_session", "")
    body, tag = cookie.rsplit(".", 1)
    assert len(tag) == 64, "a fresh sign-in is tagged in full"
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    client.cookies.clear()
    client.cookies.set("choregos_session", _legacy_cookie(get_settings().session_secret, payload))
    me = await client.get("/api/v1/me")
    assert me.status_code == 200, me.text
    assert me.json()["email"] == admin


async def test_a_session_signed_with_a_forged_tag_is_refused(client: AsyncClient, admin: str) -> None:
    cookie = client.cookies.get("choregos_session", "")
    body, _ = cookie.rsplit(".", 1)
    client.cookies.clear()
    client.cookies.set("choregos_session", f"{body}.{'0' * 64}")
    assert (await client.get("/api/v1/me")).status_code == 401


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeA2aAgent]:
    set_sealing_key(monkeypatch)
    yield from remote_agent()


async def test_a_remote_oauth_handshake_is_bound_to_its_purposes(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    """Through the real routes: what `authorize` hands out is no session and no OIDC value."""
    from choregos_api.security import read_session

    base = "/api/v1/orgs/varga/remote-credentials"
    created = await client.post(
        base, json={"name": "rovo-oauth", "type": "oauth2_authorization_code", "card_url": remote.card_url}
    )
    assert created.status_code == 201, created.text
    started = await client.post(f"{base}/rovo-oauth/authorize")
    assert started.status_code == 200, started.text
    state = parse_qs(urlsplit(started.json()["redirect_url"]).query)["state"][0]
    handshake = client.cookies.get("choregos_remote_oauth", "")
    session = client.cookies.get("choregos_session", "")
    assert state and handshake and session

    purposes = ("session", "oidc-state", "oidc-handshake", "remote-oauth-state", "remote-oauth-handshake")
    for value, own in (
        (state, "remote-oauth-state"),
        (handshake, "remote-oauth-handshake"),
        (session, "session"),
    ):
        for purpose in purposes:
            assert (read_session(value, purpose=purpose) is not None) is (purpose == own), (own, purpose)


def test_a_rotation_keeps_sessions_signed_with_the_previous_key() -> None:
    from choregos_api.config import Settings
    from choregos_api.security import read_session, sign_session

    old, new = "a" * 48, "b" * 48
    payload = {"sub": "u", "exp": int(time.time()) + 60}
    signed_with_old = sign_session(payload, Settings(session_secret=old))
    assert read_session(signed_with_old, Settings(session_secret=new, session_secret_previous=old)) == payload
    assert read_session(signed_with_old, Settings(session_secret=new)) is None
    # the previous key verifies, it never signs: what the new settings sign reads with the new key only
    signed_with_new = sign_session(payload, Settings(session_secret=new, session_secret_previous=old))
    assert read_session(signed_with_new, Settings(session_secret=new)) == payload
    assert read_session(signed_with_new, Settings(session_secret=old)) is None
    # a purpose-bound value rotates the same way
    state = sign_session(payload, Settings(session_secret=old), purpose="oidc-state")
    rotated = Settings(session_secret=new, session_secret_previous=old)
    assert read_session(state, rotated, purpose="oidc-state") == payload
    assert read_session(state, rotated) is None


def test_an_unset_previous_key_is_not_an_empty_key_anyone_can_sign_with() -> None:
    from choregos_api.config import Settings
    from choregos_api.security import read_session, sign_session

    payload = {"sub": "attacker", "exp": int(time.time()) + 60}
    settings = Settings(session_secret=LONG_SECRET, session_secret_previous="")
    # signed by someone who knows no secret: with an empty key, in both tag formats
    assert read_session(sign_session(payload, Settings(session_secret="")), settings) is None
    assert read_session(_legacy_cookie("", payload), settings) is None


def test_a_non_ascii_signature_is_refused_not_a_server_error() -> None:
    from choregos_api.security import read_session

    assert read_session("e30.é" + "0" * 63) is None
