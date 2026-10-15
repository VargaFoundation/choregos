"""The images an operator names are pinned by digest when the deployment says so (ADR 0063)."""

from __future__ import annotations

import json

import pytest
from choregos_orchestrator.config import OrchestratorSettings
from pydantic import ValidationError

PINNED = "registry.example/agent@sha256:" + "c" * 64


@pytest.mark.parametrize("variable", ["CHOREGOS_JOB_IMAGES", "CHOREGOS_AGENT_IMAGES"])
def test_a_tag_only_reference_is_refused_when_digests_are_required(
    variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_REQUIRE_IMAGE_DIGEST", "true")
    monkeypatch.setenv(variable, json.dumps({"quant": "registry.example/agent:latest"}))
    with pytest.raises(ValidationError, match="not pinned by digest"):
        OrchestratorSettings()


@pytest.mark.parametrize("variable", ["CHOREGOS_JOB_IMAGES", "CHOREGOS_AGENT_IMAGES"])
def test_a_pinned_reference_passes_and_a_tag_passes_when_not_required(
    variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(variable, json.dumps({"quant": PINNED}))
    monkeypatch.setenv("CHOREGOS_REQUIRE_IMAGE_DIGEST", "true")
    assert OrchestratorSettings()
    monkeypatch.setenv(variable, json.dumps({"quant": "registry.example/agent:latest"}))
    monkeypatch.setenv("CHOREGOS_REQUIRE_IMAGE_DIGEST", "false")
    assert OrchestratorSettings()
