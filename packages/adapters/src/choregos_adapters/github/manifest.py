# SPDX-License-Identifier: Apache-2.0
"""The GitHub App Manifest flow's last step, and where the deployment's App credentials come from.

A platform administrator registers the App on github.com from a manifest the platform builds (ADR
0161); GitHub sends the browser back with a one-hour `code`, and `POST /app-manifests/{code}/conversions`
(no authentication: the code is the proof) answers the App with its credentials: `id`, `slug`,
`client_id`, `client_secret`, `webhook_secret` and `pem`. `FakeManifestConversion`
(`fakes/github_app.py`) has the same method.

The credentials then live where the platform keeps them: the deployment's settings
(`CHOREGOS_GITHUB_APP_ID` / `_PRIVATE_KEY`), or the App registered from a manifest, sealed in the
database. Code outside the API (the adapters' GitHub client) asks `app_credentials()`, which reads the
deployment's variables first and then the provider the API registers (`set_app_credentials_provider`).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from ..errors import UpstreamError
from .auth import GITHUB_API

#: `() -> (app_id, private_key)` of the App registered from a manifest, or `None` without one.
AppCredentialsProvider = Callable[[], tuple[str, str] | None]

_PROVIDER: list[AppCredentialsProvider] = []
#: What a manifest code looks like: it is put in a URL path, so nothing else is sent.
_CODE = re.compile(r"[A-Za-z0-9_-]{1,200}")


def set_app_credentials_provider(provider: AppCredentialsProvider | None) -> None:
    """The process's source of a stored App (the API registers one); `None` removes it."""
    _PROVIDER.clear()
    if provider is not None:
        _PROVIDER.append(provider)


def app_credentials() -> tuple[str, str]:
    """`(app_id, private_key)`: the deployment's variables when they name an App, else the provider's
    stored App, else two empty strings. The deployment's settings always win (ADR 0161)."""
    app_id = os.environ.get("CHOREGOS_GITHUB_APP_ID", "")
    private_key = os.environ.get("CHOREGOS_GITHUB_APP_PRIVATE_KEY", "")
    if app_id or private_key:
        return app_id, private_key
    stored = _PROVIDER[0]() if _PROVIDER else None
    return stored if stored is not None else ("", "")


class ManifestConversionPort(Protocol):
    """Exchanges the manifest flow's `code` for the App GitHub just registered."""

    async def convert(self, code: str) -> dict[str, Any]: ...


class GitHubManifestConversion:
    """The real exchange: `POST {api}/app-manifests/{code}/conversions`, unauthenticated."""

    def __init__(self, *, base_url: str = GITHUB_API, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def convert(self, code: str) -> dict[str, Any]:
        if not _CODE.fullmatch(code or ""):
            raise UpstreamError("github", "the manifest flow's code is not one GitHub issues")
        client = self._client or httpx.AsyncClient(timeout=20)
        try:
            response = await client.post(
                f"{self._base_url}/app-manifests/{code}/conversions",
                headers={"Accept": "application/vnd.github+json"},
            )
        except httpx.HTTPError as exc:
            raise UpstreamError("github", f"the manifest conversion failed: {exc}") from exc
        finally:
            if self._client is None:
                await client.aclose()
        if response.status_code != 201:
            # 404: an unknown, used or expired code (one hour); 422: refused or rate limited.
            raise UpstreamError(
                "github", f"POST /app-manifests/{{code}}/conversions -> {response.status_code}"
            )
        body = response.json()
        if not isinstance(body, dict):
            raise UpstreamError("github", "the manifest conversion did not answer an object")
        return body
