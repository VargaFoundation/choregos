"""Unsigned GitHub and Tekton webhooks are accepted only on an instance that is not deployed (ADR 0102).

`Settings.deployed` is staging/prod, or any instance served over https (the default `env` is dev).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


async def _post(monkeypatch: pytest.MonkeyPatch, path: str, public_url: str, headers: dict[str, str]) -> int:
    from choregos_api.config import reset_settings_cache
    from choregos_api.main import create_app

    monkeypatch.setenv("CHOREGOS_ENV", "dev")
    monkeypatch.setenv("CHOREGOS_PUBLIC_URL", public_url)
    monkeypatch.setenv("CHOREGOS_GITHUB_WEBHOOK_SECRET", "")
    monkeypatch.setenv("CHOREGOS_GENERIC_WEBHOOK_SECRET", "")
    reset_settings_cache()
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return (await c.post(path, content=b"{}", headers=headers)).status_code
    finally:
        reset_settings_cache()


async def test_an_unsigned_github_webhook_is_refused_on_an_https_dev_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ping = {"X-GitHub-Event": "ping"}
    assert await _post(monkeypatch, "/api/v1/webhooks/github", "https://choregos.example.com", ping) == 401
    assert await _post(monkeypatch, "/api/v1/webhooks/github", "http://localhost:3000", ping) == 202


async def test_an_unsigned_tekton_webhook_is_refused_on_an_https_dev_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = {"Ce-Type": "dev.tekton.event.other", "Ce-Id": "x"}
    assert await _post(monkeypatch, "/api/v1/webhooks/tekton", "https://choregos.example.com", event) == 401
