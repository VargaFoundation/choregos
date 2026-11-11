"""Where the console's help links point (S62-11): `docs_url` in `GET /edition`, from `CHOREGOS_DOCS_URL`.

An air-gapped install serves its own copy of the documentation: the console must not hard-code the
public site. The value lands in an `href`, so only an `http(s)` address is served.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from httpx import AsyncClient


@pytest.fixture
def docs_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    from choregos_api.config import reset_settings_cache

    yield monkeypatch
    monkeypatch.delenv("CHOREGOS_DOCS_URL", raising=False)
    reset_settings_cache()


def _set(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_DOCS_URL", value)
    reset_settings_cache()


async def test_the_public_site_by_default(client: AsyncClient, docs_env: pytest.MonkeyPatch) -> None:
    docs_env.delenv("CHOREGOS_DOCS_URL", raising=False)
    from choregos_api.config import reset_settings_cache

    reset_settings_cache()
    body = (await client.get("/api/v1/edition")).json()
    assert body["docs_url"] == "https://docs.varga.foundation/choregos"


async def test_an_install_points_it_at_its_own_copy(
    client: AsyncClient, docs_env: pytest.MonkeyPatch
) -> None:
    _set(docs_env, "https://docs.intranet.example/choregos/")
    body = (await client.get("/api/v1/edition")).json()
    assert body["docs_url"] == "https://docs.intranet.example/choregos", "without the trailing slash"
    assert (await client.get("/edition")).json()["docs_url"] == body["docs_url"]


@pytest.mark.parametrize("value", ["javascript:alert(1)", "docs.example", "https://x.example/<script>", ""])
async def test_what_is_not_an_http_address_gives_the_public_site(
    client: AsyncClient, docs_env: pytest.MonkeyPatch, value: str
) -> None:
    _set(docs_env, value)
    body = (await client.get("/api/v1/edition")).json()
    assert body["docs_url"] == "https://docs.varga.foundation/choregos"
