# SPDX-License-Identifier: Apache-2.0
"""Creating the deployment's GitHub App from a manifest (S67-31, ADR 0161).

GitHub's App Manifest flow, three steps:

1. The console POSTs a form to github.com (`/settings/apps/new`, or
   `/organizations/<org>/settings/apps/new`) whose one field, `manifest`, is the JSON `build_manifest`
   returns; the person who registers the App there owns it.
2. GitHub sends the browser back to the manifest's `redirect_url` (`GET /github/manifest/callback`) with
   a `code` valid one hour and our signed `state`.
3. `POST /app-manifests/{code}/conversions` answers the App with its credentials, once. `register`
   seals them (ADR 0058) and stores the App: from then on it is the deployment's App, unless the
   deployment's settings name one (`github_app_credentials`).

The permissions and events are the adapters' own constants (`APP_PERMISSIONS`, `APP_EVENTS`), the ones
the GitHub procedures list and a test compares (ADR 0067): the manifest cannot ask for less, or more,
than what the platform uses. `installation` and `installation_repositories` are not in it: GitHub
delivers them to every App and does not let one subscribe to them.
"""

from __future__ import annotations

import secrets
import time
from typing import Any
from urllib.parse import quote, urlsplit

from choregos_adapters import fakes_enabled
from choregos_adapters.errors import UpstreamError
from choregos_adapters.fakes.github_app import FakeManifestConversion
from choregos_adapters.github import (
    APP_EVENTS,
    APP_PERMISSIONS,
    GitHubManifestConversion,
    ManifestConversionPort,
)
from choregos_adapters.github.app import GITHUB_WEB
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db.models import GitHubAppRegistration
from ..db.session import in_platform_scope
from ..errors import conflict, forbidden
from ..secret_store import ENV_KEKS, KeyWrapper, active_key_wrapper
from ..security import read_session, sign_session
from . import github_app_credentials as credentials

#: The purpose the state is signed for: a value signed for another purpose (a session) never reads as it.
STATE_PURPOSE = "github-app-manifest"
#: GitHub's own limit: the three steps of the flow complete within one hour.
STATE_TTL_S = 3600
#: GitHub refuses an App name longer than this.
NAME_MAX = 34
CALLBACK_PATH = "/api/v1/github/manifest/callback"
SETUP_PATH = "/api/v1/github/setup"
WEBHOOK_PATH = "/api/v1/webhooks/github"
#: Where the console shows the outcome.
CONSOLE_PAGE = "/admin/github-app"


def _api(settings: Settings) -> str:
    return settings.api_url.rstrip("/")


def webhook_url(settings: Settings) -> str:
    return _api(settings) + WEBHOOK_PATH


def callback_url(settings: Settings) -> str:
    """Where GitHub sends the browser after an installation: the Setup route, which claims it."""
    return _api(settings) + SETUP_PATH


def redirect_url(settings: Settings) -> str:
    """Where GitHub sends the browser once the App is registered, with the conversion `code`."""
    return _api(settings) + CALLBACK_PATH


def suggested_name(settings: Settings) -> str:
    host = urlsplit(settings.public_url).hostname or "instance"
    return f"Choregos {host}"[:NAME_MAX].rstrip(" .-")


def build_manifest(settings: Settings, *, name: str, public: bool, webhook_active: bool) -> dict[str, Any]:
    """The manifest GitHub registers the App from: URLs derived from this instance's settings.

    `request_oauth_on_install`: an installation asks the person to authorise the App, so the platform can
    check they manage the installation they claim on an instance with several organisations (ADR 0073);
    GitHub then sends them to the first callback URL (the Setup route) instead of a setup URL, which it
    does not accept alongside that option."""
    return {
        "name": name,
        "url": settings.public_url.rstrip("/"),
        "description": "Choregos: governed agents and human steps on this organisation's repositories.",
        "hook_attributes": {"url": webhook_url(settings), "active": webhook_active},
        "redirect_url": redirect_url(settings),
        "callback_urls": [callback_url(settings)],
        "request_oauth_on_install": True,
        "public": public,
        "default_permissions": dict(APP_PERMISSIONS),
        "default_events": list(APP_EVENTS),
    }


def sign_state(*, user: str, public: bool, webhook_active: bool, now: float | None = None) -> str:
    """The flow's `state`: who started it and the choices GitHub's answer does not repeat, signed."""
    claims = {
        "u": user,
        "p": public,
        "w": webhook_active,
        "exp": int((now if now is not None else time.time()) + STATE_TTL_S),
        "n": secrets.token_hex(8),
    }
    return sign_session(claims, purpose=STATE_PURPOSE)


def verify_state(state: str, *, user: str) -> dict[str, Any]:
    """The claims of a state this instance signed for this person, unexpired; 403 otherwise."""
    claims = read_session(state, purpose=STATE_PURPOSE)
    if claims is None:
        raise forbidden("the GitHub App creation state is not valid or expired: start again from the console")
    if claims.get("u") != user:
        raise forbidden("this GitHub App creation was started by another person")
    return claims


def action_url(organization: str | None, state: str) -> str:
    """GitHub's registration page: an organisation's when one owns the App, else the person's own."""
    owner = f"/organizations/{quote(organization)}" if organization else ""
    return f"{GITHUB_WEB}{owner}/settings/apps/new?state={quote(state)}"


def get_conversion() -> ManifestConversionPort:
    """The conversion endpoint: the scriptable fake under `CHOREGOS_FAKES`, GitHub's otherwise."""
    if fakes_enabled():
        return FakeManifestConversion.shared()
    return GitHubManifestConversion()


async def blocked_by(session: AsyncSession, settings: Settings | None = None) -> str | None:
    """Why the console cannot create an App now, or None when it can."""
    settings = settings or get_settings()
    if credentials.deployment_defines_app(settings):
        return (
            "the deployment's settings define the GitHub App (CHOREGOS_GITHUB_APP_ID / "
            "CHOREGOS_GITHUB_APP_PRIVATE_KEY): they win over an App created here"
        )
    if await credentials.stored_registration(session) is not None:
        return "a GitHub App is already registered for this instance: forget it first"
    if active_key_wrapper() is None:
        return (
            f"no secrets key ({ENV_KEKS}): the App's private key could not be sealed. "
            "Ask the operator to configure one"
        )
    return None


async def ensure_creatable(session: AsyncSession) -> KeyWrapper:
    """409 naming the reason unless an App can be created and stored now; the key to seal it with."""
    reason = await blocked_by(session)
    wrapper = active_key_wrapper()
    if reason is not None or wrapper is None:
        raise conflict(reason or f"no secrets key ({ENV_KEKS})")
    return wrapper


def _required(answer: dict[str, Any], key: str) -> str:
    value = answer.get(key)
    if value is None or value == "":
        raise UpstreamError("github", f"the manifest conversion did not return `{key}`")
    return str(value)


async def register(
    session: AsyncSession,
    *,
    code: str,
    created_by: str,
    public: bool,
    webhook_active: bool,
    conversion: ManifestConversionPort | None = None,
) -> GitHubAppRegistration:
    """Exchange the code for the App, seal its credentials and store it. Checked BEFORE the exchange,
    which GitHub answers once: nothing to store it in means the code is not spent."""
    wrapper = await ensure_creatable(session)
    answer = await (conversion or get_conversion()).convert(code)
    app_id = _required(answer, "id")
    pem = _required(answer, "pem")
    raw_owner = answer.get("owner")
    owner: dict[str, Any] = raw_owner if isinstance(raw_owner, dict) else {}
    row = GitHubAppRegistration(
        app_id=app_id,
        slug=str(answer.get("slug") or ""),
        name=str(answer.get("name") or "")[:100],
        owner_login=str(owner.get("login") or ""),
        owner_type=str(owner.get("type") or "User"),
        html_url=str(answer.get("html_url") or ""),
        public=public,
        webhook_active=webhook_active,
        client_id=_required(answer, "client_id"),
        created_by=created_by,
    )
    credentials.put_envelope(
        row,
        credentials.seal_credentials(
            wrapper,
            app_id,
            pem=pem,
            webhook_secret=_required(answer, "webhook_secret"),
            client_secret=_required(answer, "client_secret"),
        ),
    )
    async with in_platform_scope(session):  # the table is the platform's alone (RLS)
        session.add(row)
        await session.flush()
    await credentials.refresh(session, force=True)
    return row


async def forget(session: AsyncSession) -> GitHubAppRegistration | None:
    """Delete the stored App (its credentials with it); the App itself stays on GitHub."""
    row = await credentials.stored_registration(session)
    if row is None:
        return None
    async with in_platform_scope(session):
        await session.delete(row)
        await session.flush()
    credentials.forget_cache()
    return row
