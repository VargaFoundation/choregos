"""The images an operator names are pinned by digest when the deployment says so (ADR 0063)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tranibor_orchestrator.config import OrchestratorSettings

PINNED = "registry.example/agent@sha256:" + "c" * 64


@pytest.mark.parametrize("variable", ["TRANIBOR_JOB_IMAGES", "TRANIBOR_AGENT_IMAGES"])
def test_a_tag_only_reference_is_refused_when_digests_are_required(
    variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRANIBOR_REQUIRE_IMAGE_DIGEST", "true")
    monkeypatch.setenv(variable, json.dumps({"quant": "registry.example/agent:latest"}))
    with pytest.raises(ValidationError, match="not pinned by digest"):
        OrchestratorSettings()


@pytest.mark.parametrize("variable", ["TRANIBOR_JOB_IMAGES", "TRANIBOR_AGENT_IMAGES"])
def test_a_pinned_reference_passes_and_a_tag_passes_when_not_required(
    variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(variable, json.dumps({"quant": PINNED}))
    monkeypatch.setenv("TRANIBOR_REQUIRE_IMAGE_DIGEST", "true")
    assert OrchestratorSettings()
    monkeypatch.setenv(variable, json.dumps({"quant": "registry.example/agent:latest"}))
    monkeypatch.setenv("TRANIBOR_REQUIRE_IMAGE_DIGEST", "false")
    assert OrchestratorSettings()
