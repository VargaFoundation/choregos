"""`GET /api/v1/theme`: public, served from the default or a plugin's provider, never invalid."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import choregos_contracts as contracts
import jsonschema
import pytest
import structlog
from choregos_api.greffons import declare_theme_provider, reinitialiser
from httpx import AsyncClient

from .theme_fixtures import SAMPLE_THEME, sample


@pytest.fixture(autouse=True)
def _clean_seams() -> Iterator[None]:
    reinitialiser()
    yield
    reinitialiser()


def _schema() -> dict[str, Any]:
    spec = contracts.load_openapi()
    return {"$ref": "#/components/schemas/Theme", "components": spec["components"]}


async def test_without_a_provider_the_community_default_is_served(client: AsyncClient) -> None:
    response = await client.get("/api/v1/theme")  # no session: the route is public
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "default" and body["product_name"] == "Choregos"
    assert body["default_mode"] == "dark" and body["tokens"] == {"dark": {}, "light": {}}
    assert response.headers["x-choregos-theme"] == "default"
    assert response.headers["cache-control"] == "no-cache"
    jsonschema.validate(body, _schema())


async def test_a_provider_theme_is_served_and_matches_the_contract(client: AsyncClient) -> None:
    seen: list[tuple[Any, str | None]] = []

    def provider(session: Any, org: str | None) -> dict[str, Any]:
        seen.append((session, org))
        return sample()

    declare_theme_provider(provider)
    response = await client.get("/api/v1/theme")
    assert response.status_code == 200
    assert response.json() == SAMPLE_THEME
    assert response.headers["x-choregos-theme"] == "provided"
    jsonschema.validate(response.json(), _schema())
    assert seen[0][0] is not None and seen[0][1] is None


async def test_an_async_provider_is_awaited_and_gets_the_organisation(client: AsyncClient) -> None:
    orgs: list[str | None] = []

    async def provider(session: Any, org: str | None) -> dict[str, Any]:
        orgs.append(org)
        return sample(id=org or "x")

    declare_theme_provider(provider)
    response = await client.get("/api/v1/theme", params={"org": "acme"})
    assert response.json()["id"] == "acme"
    assert orgs == ["acme"]


async def test_a_provider_with_nothing_to_say_gives_the_default(client: AsyncClient) -> None:
    declare_theme_provider(lambda session, org: None)
    response = await client.get("/api/v1/theme")
    assert response.json()["id"] == "default"
    assert response.headers["x-choregos-theme"] == "default"


async def test_a_provider_that_raises_falls_back_to_the_default(client: AsyncClient) -> None:
    def provider(session: Any, org: str | None) -> dict[str, Any]:
        raise RuntimeError("the theme table is gone")

    declare_theme_provider(provider)
    response = await client.get("/api/v1/theme")
    assert response.status_code == 200
    assert response.json()["id"] == "default"
    assert response.headers["x-choregos-theme"] == "fallback"


async def test_an_invalid_theme_is_never_served_and_the_log_names_the_violation(client: AsyncClient) -> None:
    bad = sample()
    bad["tokens"]["light"]["ink"] = "#000000"
    bad["tokens"]["light"]["surface"] = "#ffffff"
    bad["tokens"]["light"]["raised"] = "#ffffff"
    declare_theme_provider(lambda session, org: bad)
    with structlog.testing.capture_logs() as logs:
        response = await client.get("/api/v1/theme")
    assert response.json()["id"] == "default"
    assert response.headers["x-choregos-theme"] == "fallback"
    event = next(entry for entry in logs if entry["event"] == "theme.invalid")
    assert any("ink on surface: too hard" in violation for violation in event["violations"])


async def test_etag_gives_a_304(client: AsyncClient) -> None:
    first = await client.get("/api/v1/theme")
    again = await client.get("/api/v1/theme", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304


async def test_a_malformed_org_is_ignored_not_an_error(client: AsyncClient) -> None:
    seen: list[str | None] = []
    declare_theme_provider(lambda session, org: seen.append(org) or None)
    assert (await client.get("/api/v1/theme", params={"org": "A b;"})).status_code == 200
    assert seen == [None]


def test_a_second_provider_stops_the_start() -> None:
    declare_theme_provider(lambda session, org: None)
    with pytest.raises(RuntimeError, match="already declared"):
        declare_theme_provider(lambda session, org: None)


def test_the_same_provider_twice_is_idempotent() -> None:
    def provider(session: Any, org: str | None) -> None:
        return None

    declare_theme_provider(provider)
    declare_theme_provider(provider)
