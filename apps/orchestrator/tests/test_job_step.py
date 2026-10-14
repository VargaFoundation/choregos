"""A job step (ADR 0056) is prepared without a model or a gateway key, in the image it names."""

from __future__ import annotations

import json
from typing import Any

import pytest
from choregos_core.redaction import REDACTED
from choregos_orchestrator.activities import stage as stage_activities
from temporalio.exceptions import ApplicationError

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

QUANT = "registry.example/choregos-runner-quant@sha256:" + "a" * 64


def _plan(setup: Fixture, image: str | None = None) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": "t-backtest",
        "role": "backtest",
        "from_state": "inbox",
        "to_state": "backtested",
        "actor": "engine",
        "attempt": 1,
        "max_minutes": 20,
        "outputs": ["metrics"],
        "output_types": {"metrics": "json"},
        "job": {"command": ["choregos-quant", "backtest", "--out", "out"], "image": image},
    }


@pytest.fixture
def images(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_orchestrator.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_JOB_IMAGES", json.dumps({"quant": QUANT}))
    reset_settings_cache()


async def test_a_job_is_prepared_without_a_model_or_a_key(setup: Fixture, images: None) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    prepared = await stage_activities.prepare_stage(_plan(setup, image="quant"))

    stage_input = prepared["stage_input"]
    assert stage_input["job"] == {
        "command": ["choregos-quant", "backtest", "--out", "out"],
        "image": QUANT,
        "tools": {},
    }
    assert stage_input["gateway_key"] is None
    assert stage_input["agent"]["backend"] == "job"
    assert stage_input["budget"]["max_minutes"] == 20
    # The job reads its data with the run token, but the token lives in the run's Secret only (#519).
    assert stage_input["callbacks"]["run_token"] in ("", None, REDACTED)
    assert setup.adapters.gateway.keys == {}, "a job must never hold a gateway key"
    async with session_scope() as session:
        run = await session.get(Run, prepared["run_id"])
        assert run is not None
        assert run.stage_input["callbacks"]["run_token"] in ("", None, REDACTED)
        assert (run.backend, run.model, run.gateway_key_id) == ("job", None, None)
        assert run.playbook_checksum.startswith(f"job:{QUANT}"[:40])

    again = await stage_activities.prepare_stage(_plan(setup, image="quant"))
    assert again["reused"] and again["run_id"] == prepared["run_id"]


async def test_an_image_the_platform_does_not_know_stops_the_step(setup: Fixture, images: None) -> None:
    with pytest.raises(ApplicationError) as raised:
        await stage_activities.prepare_stage(_plan(setup, image="miner"))
    assert raised.value.type == "job_image_unknown" and raised.value.non_retryable


async def test_a_job_starts_in_its_image_and_a_plain_job_in_the_runner_image(
    setup: Fixture, images: None
) -> None:
    from choregos_orchestrator.config import get_settings

    for image, expected in (("quant", QUANT), (None, get_settings().runner_image)):
        plan = _plan(setup, image=image)
        plan["transition_id"] = f"t-backtest-{image}"
        prepared = await stage_activities.prepare_stage(plan)
        await stage_activities.start_run(
            {
                "project_id": setup.project_id,
                "work_item_id": setup.work_item_id,
                "stage_input": prepared["stage_input"],
            }
        )
        spec = setup.adapters.executor.started[-1]
        assert spec.runner_image == expected
        # `start_run` mints a run's key for its Secret (#523): never for a job (ADR 0056).
        assert spec.gateway_key is None
        assert setup.adapters.gateway.keys == {}, "a job must never hold a gateway key"
        assert spec.run_token and spec.run_token != REDACTED, "the job calls the internal API"
