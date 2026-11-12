"""The deployment's GitHub App, created from a manifest (S67-31, ADR 0161).

GitHub's App Manifest flow: the console posts a manifest the platform built, GitHub registers the App
and sends the browser back with a one-hour code, and the platform exchanges it for the App's
credentials, which it seals (ADR 0058) and keeps. From then on that App is the deployment's App for
every reader (the installations, the run's git token, the webhook signature, the adapters), unless the
deployment's settings name one: they win.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient

from .conftest import login
from .kits.run_git_token import run_token_for, run_with_repository

# Plain http: an https URL marks the instance deployed, and the test client would not send its cookies.
PUBLIC_URL = "http://choregos.example.test"
API_URL = "http://api.choregos.example.test"
KEY_1 = "k1:" + base64.b64encode(b"1" * 32).decode()
KEY_2 = "k2:" + base64.b64encode(b"2" * 32).decode()
WEBHOOK_SECRET = "whsec-from-github-0123456789"
CLIENT_SECRET = "client-secret-from-github-0123456789"


def _pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()


PEM = _pem()


@pytest.fixture(autouse=True)
def instance(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """An instance with its own URLs and a secrets key; no App in the deployment's settings."""
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_PUBLIC_URL", PUBLIC_URL)
    monkeypatch.setenv("CHOREGOS_API_URL", API_URL)
    monkeypatch.setenv("CHOREGOS_SECRETS_KEKS", KEY_1)
    for name in (
        "CHOREGOS_GITHUB_APP_ID",
        "CHOREGOS_GITHUB_APP_PRIVATE_KEY",
        "CHOREGOS_GITHUB_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def conversion() -> Iterator[Any]:
    from choregos_adapters.fakes.github_app import FakeManifestConversion

    fake = FakeManifestConversion.shared()
    fake.reset()
    yield fake
    fake.reset()


def _settings_again() -> None:
    from choregos_api.config import reset_settings_cache

    reset_settings_cache()


async def _start(client: AsyncClient, **body: Any) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/platform/github-app/manifest", json={"name": "Choregos test", "public": False} | body
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _state_of(start: dict[str, Any]) -> str:
    return parse_qs(urlsplit(start["action_url"]).query)["state"][0]


async def _create(client: AsyncClient, conversion: Any, *, code: str = "c0de", **body: Any) -> Any:
    """The whole flow: the manifest, GitHub's registration (scripted), the callback."""
    state = _state_of(await _start(client, **body))
    conversion.register(
        code,
        id=4242,
        slug="choregos-test",
        pem=PEM,
        webhook_secret=WEBHOOK_SECRET,
        client_id="Iv1.abc",
        client_secret=CLIENT_SECRET,
        owner={"login": "acme", "type": "Organization"},
    )
    return await client.get("/api/v1/github/manifest/callback", params={"code": code, "state": state})


async def _rows() -> list[Any]:
    from choregos_api.db.models import GitHubAppRegistration
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        return list((await session.execute(select(GitHubAppRegistration))).scalars())


async def _audit(action: str) -> list[Any]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        return list((await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars())


# ───────────────────────────── the manifest ─────────────────────────────


async def test_the_manifest_asks_for_what_the_adapters_use_and_points_at_this_instance(
    client: AsyncClient, admin: str
) -> None:
    from choregos_adapters.github import APP_EVENTS, APP_PERMISSIONS

    start = await _start(client, name="Choregos acme", public=True, webhook_active=False)
    manifest = start["manifest"]
    assert manifest["default_permissions"] == APP_PERMISSIONS
    assert manifest["default_events"] == list(APP_EVENTS)
    assert "installation" not in manifest["default_events"], "GitHub delivers it to every App, unsubscribable"
    assert manifest["public"] is True
    assert manifest["name"] == "Choregos acme"
    assert manifest["url"] == PUBLIC_URL
    assert manifest["hook_attributes"] == {"url": f"{API_URL}/api/v1/webhooks/github", "active": False}
    assert manifest["redirect_url"] == f"{API_URL}/api/v1/github/manifest/callback"
    assert manifest["callback_urls"] == [f"{API_URL}/api/v1/github/setup"]
    assert manifest["request_oauth_on_install"] is True
    assert "setup_url" not in manifest, "GitHub accepts no setup URL alongside request_oauth_on_install"
    assert start["action_url"].startswith("https://github.com/settings/apps/new?state=")

    owned = await _start(client, organization="acme-inc")
    assert owned["action_url"].startswith(
        "https://github.com/organizations/acme-inc/settings/apps/new?state="
    )
    assert owned["manifest"]["public"] is False


async def test_public_or_private_is_an_explicit_choice(client: AsyncClient, admin: str) -> None:
    response = await client.post("/api/v1/platform/github-app/manifest", json={"name": "Choregos test"})
    assert response.status_code == 422, response.text
    assert "public" in response.text


async def test_the_status_says_what_a_new_app_would_get(client: AsyncClient, admin: str) -> None:
    from choregos_adapters.github import APP_EVENTS, APP_PERMISSIONS

    response = await client.get("/api/v1/platform/github-app")
    assert response.status_code == 200, response.text
    status = response.json()
    assert status["source"] == "none"
    assert status["usable"] is False
    assert status["blocked_by"] is None
    assert status["permissions"] == APP_PERMISSIONS
    assert status["events"] == list(APP_EVENTS)
    assert status["webhook_url"] == f"{API_URL}/api/v1/webhooks/github"
    assert status["suggested_name"] == "Choregos choregos.example.test"[:34].rstrip(" .-")


async def test_only_a_platform_administrator_reads_or_starts_it(client: AsyncClient, org: str) -> None:
    await login(client, "dev@varga.dev")
    assert (await client.get("/api/v1/platform/github-app")).status_code == 403
    started = await client.post("/api/v1/platform/github-app/manifest", json={"name": "x", "public": False})
    assert started.status_code == 403
    assert (await client.delete("/api/v1/platform/github-app")).status_code == 403


# ───────────────────────────── refusals: nothing stored ─────────────────────────────


async def test_the_deployments_app_wins_and_the_console_creates_none(
    client: AsyncClient, admin: str, conversion: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_GITHUB_APP_ID", "777")
    monkeypatch.setenv("CHOREGOS_GITHUB_APP_PRIVATE_KEY", PEM)
    _settings_again()
    status = (await client.get("/api/v1/platform/github-app")).json()
    assert status["source"] == "deployment"
    assert status["app_id"] == "777"
    assert "CHOREGOS_GITHUB_APP_ID" in status["blocked_by"]
    refused = await client.post("/api/v1/platform/github-app/manifest", json={"name": "x", "public": False})
    assert refused.status_code == 409, refused.text
    assert "CHOREGOS_GITHUB_APP_ID" in refused.text


async def test_without_a_secrets_key_nothing_is_started_and_the_code_is_not_spent(
    client: AsyncClient, admin: str, conversion: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.services.github_app_manifest import sign_state

    monkeypatch.delenv("CHOREGOS_SECRETS_KEKS")
    refused = await client.post("/api/v1/platform/github-app/manifest", json={"name": "x", "public": False})
    assert refused.status_code == 409, refused.text
    assert "CHOREGOS_SECRETS_KEKS" in refused.text
    # A state signed while a key existed, the key gone since: the callback spends nothing.
    conversion.register("c0de", pem=PEM)
    state = sign_state(user=admin, public=False, webhook_active=True)
    response = await client.get("/api/v1/github/manifest/callback", params={"code": "c0de", "state": state})
    assert response.status_code == 409, response.text
    assert conversion.calls == [], "the one-time code must not be exchanged when nothing can store the App"
    assert await _rows() == []


@pytest.mark.parametrize("case", ["forged", "expired", "another person"])
async def test_a_state_that_is_not_this_persons_is_refused(
    client: AsyncClient, admin: str, conversion: Any, case: str
) -> None:
    from choregos_api.security import sign_session
    from choregos_api.services.github_app_manifest import sign_state

    if case == "forged":
        state = sign_session({"u": admin, "p": False, "exp": time.time() + 600}, purpose="session")
    elif case == "expired":
        state = sign_state(user=admin, public=False, webhook_active=True, now=time.time() - 3700)
    else:
        state = sign_state(user="someone@varga.dev", public=False, webhook_active=True)
    conversion.register("c0de", pem=PEM)
    response = await client.get("/api/v1/github/manifest/callback", params={"code": "c0de", "state": state})
    assert response.status_code == 403, response.text
    assert conversion.calls == []
    assert await _rows() == []


async def test_a_code_github_refuses_stores_nothing(client: AsyncClient, admin: str, conversion: Any) -> None:
    state = _state_of(await _start(client))
    conversion.refuse("used-code", 404)
    response = await client.get(
        "/api/v1/github/manifest/callback", params={"code": "used-code", "state": state}
    )
    assert response.status_code == 502, response.text
    assert await _rows() == []
    assert await _audit("github_app.create") == []


async def test_without_a_session_the_callback_sends_the_browser_to_sign_in(
    client: AsyncClient, org: str
) -> None:
    response = await client.get("/api/v1/github/manifest/callback", params={"code": "c0de", "state": "s"})
    assert response.status_code == 302
    assert response.headers["location"].startswith("/api/v1/auth/login?redirect_to=")


# ───────────────────────────── success ─────────────────────────────


async def test_the_app_is_sealed_stored_audited_and_shown(
    client: AsyncClient, admin: str, conversion: Any
) -> None:
    from choregos_api.secret_store import active_key_wrapper
    from choregos_api.services.github_app_credentials import open_registration

    response = await _create(client, conversion)
    assert response.status_code == 302, response.text
    assert response.headers["location"] == f"{PUBLIC_URL}/admin/github-app?github_app=created"
    (row,) = await _rows()
    assert (row.app_id, row.slug, row.owner_login, row.public, row.client_id) == (
        "4242",
        "choregos-test",
        "acme",
        False,
        "Iv1.abc",
    )
    for secret in (PEM, WEBHOOK_SECRET, CLIENT_SECRET):
        assert secret.encode() not in row.ciphertext, "a secret is stored in clear"
    wrapper = active_key_wrapper()
    assert wrapper is not None
    opened = open_registration(row, wrapper)
    assert (opened.private_key, opened.webhook_secret, opened.client_secret) == (
        PEM,
        WEBHOOK_SECRET,
        CLIENT_SECRET,
    )
    (entry,) = await _audit("github_app.create")
    assert entry.target_id == "4242"
    assert PEM not in json.dumps(entry.payload)

    status = (await client.get("/api/v1/platform/github-app")).json()
    assert status["source"] == "manifest"
    assert (status["app_id"], status["slug"], status["public"], status["usable"]) == (
        "4242",
        "choregos-test",
        False,
        True,
    )
    assert "already registered" in status["blocked_by"]
    again = await client.post("/api/v1/platform/github-app/manifest", json={"name": "x", "public": True})
    assert again.status_code == 409, "one App per instance"


async def test_the_stored_app_is_the_app_of_every_reader(
    client: AsyncClient, project: dict[str, Any], conversion: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_adapters import builtins
    from choregos_adapters.github import GitHubApp, GitHubAppAuth, InstallationToken, app_credentials
    from choregos_api.services import github_app as svc

    assert (await _create(client, conversion)).status_code == 302

    # The adapters' GitHub client (the orchestrator builds it through the same provider).
    assert app_credentials() == ("4242", PEM)
    built = builtins._github_client({"repo": "acme/billing"})
    assert (built.auth.app_id, built.auth.private_key) == ("4242", PEM)

    # The installations' port, outside the fakes: the real App with the OAuth client GitHub issued.
    monkeypatch.setenv("CHOREGOS_FAKES", "0")
    real = svc.get_github_app()
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    assert isinstance(real, GitHubApp)
    assert real.oauth_enabled, "the conversion's client id and secret make multi-organisation claims work"

    # The run's git token.
    minted: list[str] = []

    async def mint(self: Any, http: Any, repo: str, **options: Any) -> InstallationToken:
        minted.append(self.app_id)
        return InstallationToken(token="ghs_run", expires_at=time.time() + 3600, repositories=(repo,))

    monkeypatch.setattr(GitHubAppAuth, "token_for", mint)
    run_id = await run_with_repository(project, "https://github.com/varga/billing-api.git")
    token = await client.post(f"/api/v1/internal/runs/{run_id}/git-token", headers=run_token_for(run_id))
    assert token.json()["token"] == "ghs_run", token.text
    assert minted == ["4242"]

    # The webhook: a delivery signed with the secret GitHub generated is accepted, another refused.
    body = json.dumps({"zen": "Keep it logically awesome."}).encode()
    headers = {"X-GitHub-Event": "ping", "X-GitHub-Delivery": "d-1", "content-type": "application/json"}
    signed = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    accepted = await client.post(
        "/api/v1/webhooks/github", content=body, headers={**headers, "X-Hub-Signature-256": signed}
    )
    assert accepted.status_code == 202, accepted.text
    forged = "sha256=" + hmac.new(b"another", body, hashlib.sha256).hexdigest()
    refused = await client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={**headers, "X-GitHub-Delivery": "d-2", "X-Hub-Signature-256": forged},
    )
    assert refused.status_code == 401, "a stored App's deliveries are verified, even off a deployment"


async def test_the_deployments_settings_win_over_a_stored_app(
    client: AsyncClient, admin: str, conversion: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_adapters.github import app_credentials
    from choregos_api.services import github_app_credentials as credentials

    assert (await _create(client, conversion)).status_code == 302
    monkeypatch.setenv("CHOREGOS_GITHUB_APP_ID", "999")
    monkeypatch.setenv("CHOREGOS_GITHUB_APP_PRIVATE_KEY", "deployment-key")
    monkeypatch.setenv("CHOREGOS_GITHUB_WEBHOOK_SECRET", "deployment-webhook-secret")
    _settings_again()
    found = credentials.current()
    assert found is not None and (found.app_id, found.source) == ("999", "deployment")
    assert credentials.webhook_secret() == "deployment-webhook-secret"
    assert app_credentials() == ("999", "deployment-key")
    status = (await client.get("/api/v1/platform/github-app")).json()
    assert (status["source"], status["app_id"], status["slug"]) == ("deployment", "999", None)


async def test_a_stored_app_is_forgotten_and_audited(
    client: AsyncClient, admin: str, conversion: Any
) -> None:
    from choregos_api.services import github_app_credentials as credentials

    assert (await _create(client, conversion)).status_code == 302
    forgotten = await client.delete("/api/v1/platform/github-app")
    assert forgotten.status_code == 204, forgotten.text
    assert await _rows() == []
    assert credentials.current() is None
    (entry,) = await _audit("github_app.forget")
    assert entry.target_id == "4242"
    assert (await client.get("/api/v1/platform/github-app")).json()["source"] == "none"
    assert (await client.delete("/api/v1/platform/github-app")).status_code == 404


async def test_a_key_rotation_moves_the_stored_app_to_the_new_key(
    client: AsyncClient, admin: str, conversion: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services import github_app_credentials as credentials

    assert (await _create(client, conversion)).status_code == 302
    monkeypatch.setenv("CHOREGOS_SECRETS_KEKS", f"{KEY_2},{KEY_1}")
    rewrapped = await client.post("/api/v1/platform/secrets/rewrap")
    assert rewrapped.status_code == 200, rewrapped.text
    assert rewrapped.json()["rewrapped"] >= 1
    (row,) = await _rows()
    assert row.kek_id == "k2"
    monkeypatch.setenv("CHOREGOS_SECRETS_KEKS", KEY_2)  # the old key dropped
    async with session_scope(orgs="*") as session:
        found = await credentials.refresh(session, force=True)
    assert found is not None and found.private_key == PEM


async def test_under_the_fakes_a_stored_app_configures_the_fake(
    client: AsyncClient, admin: str, conversion: Any
) -> None:
    """The demo: after the flow, the console's GitHub screens see an App (the scriptable fake)."""
    from choregos_adapters.fakes.github_app import FakeGitHubApp
    from choregos_api.services import github_app as svc

    FakeGitHubApp.shared().reset()
    assert svc.get_github_app() is None
    assert (await _create(client, conversion)).status_code == 302
    assert svc.get_github_app() is FakeGitHubApp.shared()
    assert os.environ.get("CHOREGOS_FAKES") == "1"
