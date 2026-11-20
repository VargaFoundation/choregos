"""The Jenkins webhook: shared secret (header or URL), normalisation, deduplication (S13-10)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

SECRET = "s3cret-jenkins"
NOTIFICATION = {
    "name": "app",
    "build": {
        "number": 7,
        "phase": "COMPLETED",
        "status": "SUCCESS",
        "scm": {
            "url": "https://github.com/northwind/app.git",
            "branch": "origin/choregos/12-fix",
            "commit": "abc",
        },
    },
}


@pytest.fixture
def secret(monkeypatch: pytest.MonkeyPatch) -> Any:
    from tranibor_api.config import reset_settings_cache

    monkeypatch.setenv("TRANIBOR_GENERIC_WEBHOOK_SECRET", SECRET)
    reset_settings_cache()
    yield
    reset_settings_cache()


async def test_the_secret_travels_in_the_header_or_the_url_and_nothing_else_passes(
    client: AsyncClient, project: dict[str, Any], secret: Any
) -> None:
    url = "/api/v1/webhooks/jenkins"
    assert (await client.post(url, json=NOTIFICATION)).status_code == 401
    assert (
        await client.post(url, json=NOTIFICATION, headers={"x-choregos-secret": "non"})
    ).status_code == 401
    assert (await client.post(url, json=NOTIFICATION, params={"token": "non"})).status_code == 401
    by_header = {**NOTIFICATION, "name": "by-header"}
    assert (await client.post(url, json=by_header, headers={"x-choregos-secret": SECRET})).status_code == 202
    by_url = {**NOTIFICATION, "name": "by-url"}
    assert (await client.post(url, json=by_url, params={"token": SECRET})).status_code == 202


async def test_a_redelivered_notification_is_a_duplicate_and_an_uninteresting_phase_is_not_stored(
    client: AsyncClient, project: dict[str, Any], secret: Any
) -> None:
    url = "/api/v1/webhooks/jenkins"
    headers = {"x-choregos-secret": SECRET}
    first = await client.post(url, json=NOTIFICATION, headers=headers)
    again = await client.post(url, json=NOTIFICATION, headers=headers)
    assert first.json()["duplicate"] is False and again.json()["duplicate"] is True
    queued = {**NOTIFICATION, "build": {**NOTIFICATION["build"], "phase": "QUEUED"}}
    answer = await client.post(url, json=queued, headers=headers)
    assert answer.status_code == 202 and answer.json().get("events", 0) == 0


async def test_without_a_secret_the_webhook_is_refused_outside_development(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tranibor_api import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "env", "prod")
    monkeypatch.setattr(settings, "generic_webhook_secret", "")
    refusal = await client.post("/api/v1/webhooks/jenkins", json=NOTIFICATION)
    assert refusal.status_code == 401 and "TRANIBOR_GENERIC_WEBHOOK_SECRET" in refusal.text
