"""The workers do not start on the fakes in staging and prod, and tell every run where it runs (S80-03)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from types import SimpleNamespace

import pytest
from tranibor_core.production import FakesInProductionError


class _Joined(Exception):  # noqa: N818 - the test's stop signal: the worker got as far as Temporal
    pass


@pytest.fixture
def joined(monkeypatch: pytest.MonkeyPatch) -> None:
    from temporalio.client import Client

    async def join(*_: object, **__: object) -> None:
        raise _Joined

    monkeypatch.setattr(Client, "connect", join)


@pytest.fixture
def settings_from(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    from tranibor_orchestrator.config import reset_settings_cache

    def apply(**variables: str) -> None:
        for name, value in variables.items():
            monkeypatch.setenv(name, value)
        reset_settings_cache()

    yield apply
    reset_settings_cache()


@pytest.mark.parametrize("environment", ["staging", "prod"])
async def test_the_workers_refuse_the_fakes_in_production(
    joined: None, settings_from: Callable[..., None], environment: str
) -> None:
    from tranibor_orchestrator import worker

    settings_from(CHOREGOS_ENV=environment, CHOREGOS_FAKES="1")
    with pytest.raises(FakesInProductionError) as refused:
        await worker.run_worker(["orchestrator"])
    assert "CHOREGOS_FAKES" in str(refused.value) and f"CHOREGOS_ENV={environment}" in str(refused.value)


async def test_the_workers_refuse_to_hand_the_fakes_to_every_run_in_production(
    joined: None, settings_from: Callable[..., None]
) -> None:
    from tranibor_orchestrator import worker

    settings_from(
        CHOREGOS_ENV="prod", CHOREGOS_FAKES="false", CHOREGOS_RUNNER_ENV=json.dumps({"CHOREGOS_FAKES": "yes"})
    )
    with pytest.raises(FakesInProductionError, match="CHOREGOS_RUNNER_ENV"):
        await worker.run_worker(["orchestrator"])


@pytest.mark.parametrize(
    ("environment", "fakes"), [("dev", "1"), ("test", "1"), ("prod", "0"), ("staging", "false")]
)
async def test_otherwise_the_workers_start(
    joined: None, settings_from: Callable[..., None], environment: str, fakes: str
) -> None:
    from tranibor_orchestrator import worker

    settings_from(CHOREGOS_ENV=environment, CHOREGOS_FAKES=fakes)
    with pytest.raises(_Joined):
        await worker.run_worker(["orchestrator"])


def test_every_run_is_told_the_deployments_environment() -> None:
    """The runner can only refuse the fakes in prod if it knows it is in prod; the deployment's
    value wins over one written in `CHOREGOS_RUNNER_ENV`."""
    from tranibor_orchestrator.activities.execution import runner_environment

    settings = SimpleNamespace(
        env="prod", runner_env={"CHOREGOS_ENV": "dev", "TZ": "UTC"}, runner_egress_proxy=""
    )
    assert runner_environment(settings, "choregos") == {"CHOREGOS_ENV": "prod", "TZ": "UTC"}
