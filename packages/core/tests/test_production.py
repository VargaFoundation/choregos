"""What production is, and the fakes it refuses (S80-03)."""

from __future__ import annotations

import pytest
from tranibor_core.production import (
    PRODUCTION_ENVIRONMENTS,
    FakesInProductionError,
    fakes_on,
    refuse_fakes_in_production,
    refuse_fakes_in_this_environment,
)


def test_production_is_staging_and_prod_as_everywhere_else() -> None:
    """The same two values that refuse a superuser, the development sign-in and `verify_tls: false`."""
    assert {"staging", "prod"} == PRODUCTION_ENVIRONMENTS


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " y ", "t", True])
def test_the_fakes_are_on_as_the_settings_read_them(value: str | bool) -> None:
    assert fakes_on(value)


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", None, False])
def test_and_off_otherwise(value: str | bool | None) -> None:
    assert not fakes_on(value)


@pytest.mark.parametrize("environment", ["staging", "prod", " PROD "])
def test_the_fakes_are_refused_in_production_naming_both_settings(environment: str) -> None:
    with pytest.raises(FakesInProductionError) as refused:
        refuse_fakes_in_production(environment=environment, fakes="1", process="thing")
    message = str(refused.value)
    assert "CHOREGOS_FAKES" in message and f"CHOREGOS_ENV={environment.strip()}" in message
    assert "the thing refuses to start" in message and "global.fakes" in message


@pytest.mark.parametrize(("environment", "fakes"), [("dev", "1"), ("test", True), (None, "1"), ("prod", "0")])
def test_a_bench_or_production_without_fakes_passes(environment: str | None, fakes: str | bool) -> None:
    refuse_fakes_in_production(environment=environment, fakes=fakes, process="thing")


def test_read_from_a_process_environment() -> None:
    with pytest.raises(FakesInProductionError):
        refuse_fakes_in_this_environment("thing", {"CHOREGOS_ENV": "prod", "CHOREGOS_FAKES": "yes"})
    refuse_fakes_in_this_environment("thing", {"CHOREGOS_FAKES": "yes"})
