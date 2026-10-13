"""The model resolver of `prepare_stage` reads the gateway's catalogue.

It was built with the gateway URL only (`ModelResolver(gateway_url=...)`): behind an alias such as
`platform/standard`, `provider_model` stayed empty, and the runner's `claude-code` backend — which
only takes a model it can see is a Claude — refused every run on `platform/*` before calling any
model. Seen on the local install on 2026-10-12 (S28-02): "the backend `claude-code` only accepts
claude / anthropic models (got `platform/standard`)".
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_contracts import ModelRef

from .conftest import Fixture


async def _prepare(setup: Fixture) -> dict[str, Any]:
    from choregos_orchestrator.activities import stage as activities

    return await activities.prepare_stage(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "transition_id": "t-implement",
            "role": "implement",
            "actor": "dev",
            "from_state": "ready",
            "to_state": "in_progress",
            "attempt": 1,
        }
    )


async def test_the_model_behind_the_alias_reaches_the_runner(setup: Fixture) -> None:
    from choregos_runner.backends import get_backend

    prepared = await _prepare(setup)
    model = ModelRef.model_validate(prepared["stage_input"]["model"])
    assert model.litellm_model.startswith("platform/")
    assert model.provider_model is not None and "claude" in model.provider_model
    get_backend("claude-code").validate_model(model)  # raised before the fix


async def test_a_gateway_that_cannot_list_its_models_does_not_stop_the_stage(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The catalogue sharpens the resolution; it must not become a new way for a stage to die."""

    async def unreachable() -> list[Any]:
        raise ConnectionError("gateway unreachable")

    monkeypatch.setattr(setup.adapters.gateway, "list_models", unreachable)
    prepared = await _prepare(setup)
    assert prepared["stage_input"]["model"]["litellm_model"].startswith("platform/")
