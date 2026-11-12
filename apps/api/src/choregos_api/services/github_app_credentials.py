# SPDX-License-Identifier: Apache-2.0
"""Where the deployment's GitHub App credentials come from: one place (S67-31, ADR 0161).

Two sources, in this order:

1. **The deployment's settings** (`CHOREGOS_GITHUB_APP_ID`, `_PRIVATE_KEY`, `_CLIENT_ID`,
   `_CLIENT_SECRET`, `CHOREGOS_GITHUB_WEBHOOK_SECRET`): when they name an App, it is the App, and an
   App registered from a manifest is ignored. An operator's explicit configuration always wins.
2. **The App a platform administrator registered from a manifest** (`github_app_registrations`): its
   private key, webhook secret and client secret are one envelope sealed under the deployment's key
   (ADR 0058), opened by this module only.

The stored App is kept in a process cache refreshed from the database at most every `REFRESH_S`
seconds by the async paths that hold a session (`refresh`): the routes that use the App, the pinning of
a GitHub connector (which the orchestrator calls before building one), the run's git token, the
knowledge sources and the GitHub webhook. The sync readers (`current`, the adapters' provider) read the
cache. A replica other than the one that registered the App learns of it within `REFRESH_S`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Literal

from choregos_adapters.github import set_app_credentials_provider
from choregos_core.redaction import remember_secret
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db.models import GitHubAppRegistration
from ..db.session import in_platform_scope
from ..logging import get_logger
from ..secret_store import (
    ALGORITHM,
    KeyWrapper,
    Sealed,
    SecretStoreUnavailable,
    active_key_wrapper,
    open_sealed,
    seal,
)

logger = get_logger("choregos.github_app_credentials")

#: How long a replica serves its cached copy of the stored App before reading the database again.
REFRESH_S = 60.0
#: The pseudo-organisation of the envelope's additional data: the App is the instance's, no tenant's.
#: An organisation id is a UUID, so no organisation's sealed value can ever be moved onto it.
PLATFORM_SCOPE = "platform"
ENVELOPE_VERSION = 1


def envelope_name(app_id: str) -> str:
    return f"github-app/{app_id}"


@dataclass(frozen=True)
class AppCredentials:
    """What the platform needs to act as its GitHub App, and where it came from."""

    app_id: str
    private_key: str
    webhook_secret: str
    client_id: str
    client_secret: str
    source: Literal["deployment", "manifest"]


@dataclass
class _Cache:
    stored: AppCredentials | None = None
    #: `time.monotonic()` of the last read of the database; None: never read.
    loaded_at: float | None = None


_CACHE = _Cache()


def deployment_defines_app(settings: Settings | None = None) -> bool:
    """The deployment's settings name an App (an id or a key): they are the App, nothing else is."""
    settings = settings or get_settings()
    return bool(settings.github_app_id.strip() or settings.github_app_private_key.strip())


def _from_deployment(settings: Settings) -> AppCredentials | None:
    if not (settings.github_app_id and settings.github_app_private_key):
        return None
    return AppCredentials(
        app_id=settings.github_app_id,
        private_key=settings.github_app_private_key,
        webhook_secret=settings.github_webhook_secret,
        client_id=settings.github_app_client_id,
        client_secret=settings.github_app_client_secret,
        source="deployment",
    )


def current(settings: Settings | None = None) -> AppCredentials | None:
    """The App, as this process knows it now: the deployment's, else the cached stored one."""
    settings = settings or get_settings()
    if deployment_defines_app(settings):
        return _from_deployment(settings)
    return _CACHE.stored


def webhook_secret(settings: Settings | None = None) -> str:
    """The secret GitHub signs the App's deliveries with: the stored App's when it is the App."""
    settings = settings or get_settings()
    found = current(settings)
    if found is not None and found.source == "manifest":
        return found.webhook_secret
    return settings.github_webhook_secret


async def refresh(session: AsyncSession, *, force: bool = False) -> AppCredentials | None:
    """`current()`, after reading the stored App again when the cached copy is older than `REFRESH_S`."""
    settings = get_settings()
    if deployment_defines_app(settings):
        return _from_deployment(settings)
    loaded = _CACHE.loaded_at
    if force or loaded is None or time.monotonic() - loaded >= REFRESH_S:
        _CACHE.stored = await load_stored(session)
        _CACHE.loaded_at = time.monotonic()
    return _CACHE.stored


def forget_cache() -> None:
    """Drop the cached copy (tests, and after this process deletes the stored App)."""
    _CACHE.stored = None
    _CACHE.loaded_at = None


async def stored_registration(session: AsyncSession) -> GitHubAppRegistration | None:
    async with in_platform_scope(session):
        return (
            (await session.execute(select(GitHubAppRegistration).order_by(GitHubAppRegistration.created_at)))
            .scalars()
            .first()
        )


def _sealed_of(row: GitHubAppRegistration) -> Sealed:
    return Sealed(row.ciphertext, row.nonce, row.wrapped_dek, row.dek_nonce, row.kek_id)


def open_registration(row: GitHubAppRegistration, wrapper: KeyWrapper) -> AppCredentials:
    """The credentials of a stored App, its envelope opened under the deployment's key."""
    opened = json.loads(
        open_sealed(_sealed_of(row), wrapper, PLATFORM_SCOPE, envelope_name(row.app_id), ENVELOPE_VERSION)
    )
    values = {key: str(opened.get(key) or "") for key in ("pem", "webhook_secret", "client_secret")}
    for value in values.values():
        remember_secret(value)
    return AppCredentials(
        app_id=row.app_id,
        private_key=values["pem"],
        webhook_secret=values["webhook_secret"],
        client_id=row.client_id,
        client_secret=values["client_secret"],
        source="manifest",
    )


async def load_stored(session: AsyncSession) -> AppCredentials | None:
    """The stored App, opened; None without one, or when the deployment's key cannot open it (logged)."""
    row = await stored_registration(session)
    if row is None:
        return None
    wrapper = active_key_wrapper()
    if wrapper is None:
        logger.warning("github_app.key_missing", app_id=row.app_id)
        return None
    try:
        return open_registration(row, wrapper)
    except (SecretStoreUnavailable, ValueError) as refusal:
        logger.warning("github_app.unsealable", app_id=row.app_id, error=str(refusal))
        return None


def seal_credentials(
    wrapper: KeyWrapper, app_id: str, *, pem: str, webhook_secret: str, client_secret: str
) -> Sealed:
    payload = json.dumps({"pem": pem, "webhook_secret": webhook_secret, "client_secret": client_secret})
    return seal(payload, wrapper, PLATFORM_SCOPE, envelope_name(app_id), ENVELOPE_VERSION)


def put_envelope(row: GitHubAppRegistration, sealed: Sealed) -> None:
    row.ciphertext, row.nonce = sealed.ciphertext, sealed.nonce
    row.wrapped_dek, row.dek_nonce, row.kek_id = sealed.wrapped_dek, sealed.dek_nonce, sealed.kek_id
    row.algorithm = ALGORITHM


async def rewrap(session: AsyncSession, wrapper: KeyWrapper) -> int:
    """After a key rotation: the stored App's envelope sealed again under the active key (0 or 1)."""
    row = await stored_registration(session)
    if row is None or row.kek_id == wrapper.active_id:
        return 0
    opened = open_registration(row, wrapper)
    async with in_platform_scope(session):
        put_envelope(
            row,
            seal_credentials(
                wrapper,
                row.app_id,
                pem=opened.private_key,
                webhook_secret=opened.webhook_secret,
                client_secret=opened.client_secret,
            ),
        )
        await session.flush()
    return 1


def _provider() -> tuple[str, str] | None:
    """The adapters' view of the stored App (`choregos_adapters.github.app_credentials`)."""
    found = None if deployment_defines_app() else _CACHE.stored
    return (found.app_id, found.private_key) if found is not None else None


set_app_credentials_provider(_provider)
