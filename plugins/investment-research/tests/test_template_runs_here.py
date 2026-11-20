# SPDX-License-Identifier: Apache-2.0
"""The published template runs on this commit's halves of the plugin (S83-07, ADR 0181 O4-3).

The template is the plugin's content (the platform's plugin repository, read here from the archive the core
ships offline); its job steps run the command this distribution declares, in the job image the release
builds, and its agents declare their five components (ADR 0127 §7), which the core's own check of the
templates it ships no longer covers since the template left them.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from tranibor_core.agent_completeness import missing_components
from tranibor_core.dsl import parse_workflow

from .content import TEMPLATE

PLUGIN = Path(__file__).resolve().parents[1]


def _jobs() -> list[Any]:
    workflow, report = parse_workflow((TEMPLATE / "workflows" / "campaign.yaml").read_text(encoding="utf-8"))
    assert workflow is not None, report
    return [a for a in workflow.actors.values() if getattr(a, "type", None) == "job"]


def test_every_job_step_runs_the_distributions_command_in_the_plugins_job_image() -> None:
    scripts = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    jobs = _jobs()
    assert len(jobs) == 3, "snapshot, profile, backtest"
    assert {job.image for job in jobs} == {"investment-research"}
    assert {job.command[0] for job in jobs} == {"choregos-quant"} and "choregos-quant" in scripts
    assert {job.command[1] for job in jobs} == {"snapshot", "profile", "backtest"}


def test_the_snapshot_calls_the_extensions_connector_operation() -> None:
    (snapshot,) = [job for job in _jobs() if job.command[1] == "snapshot"]
    assert [(t.connector, t.tools) for t in snapshot.tools] == [("marketdata", ["get_bars"])]
    manifest = yaml.safe_load((TEMPLATE / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["requires"]["org_connectors"] == {"marketdata": "marketdata"}


@pytest.mark.parametrize("path", sorted((TEMPLATE / "agents").glob("*.yaml")), ids=lambda p: p.stem)
def test_a_template_agent_declares_its_five_components(path: Path) -> None:
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert missing_components(document["spec"]) == []
