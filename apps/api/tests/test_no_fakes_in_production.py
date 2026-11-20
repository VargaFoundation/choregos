"""The API and the outposts' door do not start on the fakes in staging and prod (S80-03).

Under `TRANIBOR_FAKES`, without a key, the secret store is an in-memory fake and the egress control only
warns. The refusal is the first thing the start-up does after its logging, as the superuser's is.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from tranibor_core.production import FakesInProductionError


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Iterator[str]:
    from tranibor_api.config import reset_settings_cache

    environment = str(request.param)
    monkeypatch.setenv("TRANIBOR_ENV", environment)
    monkeypatch.setenv("TRANIBOR_FAKES", "1")
    # the development sign-in is refused in staging and prod by the settings themselves
    monkeypatch.setenv("TRANIBOR_DEV_LOGIN_ENABLED", "false")
    reset_settings_cache()
    yield environment
    reset_settings_cache()


def _api() -> Any:
    from tranibor_api.main import create_app

    return create_app()


@pytest.mark.parametrize("production", ["staging", "prod"], indirect=True)
async def test_the_api_refuses_to_start_on_the_fakes(production: str) -> None:
    from tranibor_api import main

    with pytest.raises(FakesInProductionError) as refused:
        async with main.lifespan(_api()):
            pass
    assert "TRANIBOR_FAKES" in str(refused.value) and f"TRANIBOR_ENV={production}" in str(refused.value)
    assert "the API refuses to start" in str(refused.value)


@pytest.mark.parametrize("production", ["staging", "prod"], indirect=True)
async def test_the_outposts_door_served_alone_refuses_them_too(production: str) -> None:
    from fastapi import FastAPI
    from tranibor_api.outposts import alone

    # Its lifespan, on any application: `create_app` itself wants outposts switched on.
    with pytest.raises(FakesInProductionError, match="outposts' door"):
        async with alone.lifespan(FastAPI()):
            pass
