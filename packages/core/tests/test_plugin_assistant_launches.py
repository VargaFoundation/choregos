# SPDX-License-Identifier: Apache-2.0
"""The launches an assistant declares (ADR 0158, S66-22): what `choregos plugin validate` refuses beyond the
schema, each with the file and the JSON path of the field."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_core.plugins import PluginError, load_directory

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugins" / "investment-assistant"
ASSISTANT = "assistants/investment-assistant.yaml"
LAUNCH = {
    "name": "portfolio_review",
    "description": "Ask the review team for a portfolio review document.",
    "input_schema": {
        "type": "object",
        "required": ["portfolio"],
        "properties": {"portfolio": {"type": "string"}},
    },
}


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    copy = tmp_path / "investment-assistant"
    shutil.copytree(FIXTURE, copy)
    return copy


def _edit(path: Path, change: Callable[[Any], None]) -> None:
    doc = yaml.safe_load(path.read_text())
    change(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def _problems(root: Path) -> list[str]:
    with pytest.raises(PluginError) as caught:
        load_directory(root)
    return caught.value.problems


def test_a_launch_is_accepted_and_names_no_project(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d.update(launches=[LAUNCH]))
    loaded = load_directory(plugin)
    assert loaded.assistants["investment-assistant"]["launches"][0]["name"] == "portfolio_review"


def test_a_launch_that_names_a_project_is_refused_by_the_schema(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d.update(launches=[{**LAUNCH, "project": "reviews"}]))
    assert any(p.startswith(f"{ASSISTANT}:launches/0: ") for p in _problems(plugin))


def test_a_launch_s_inputs_are_an_object(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d.update(launches=[{**LAUNCH, "input_schema": {"type": "string"}}]))
    assert any(p.startswith(f"{ASSISTANT}:launches/0/input_schema") for p in _problems(plugin))


def test_a_launch_s_inputs_are_a_valid_json_schema(plugin: Path) -> None:
    broken = {"type": "object", "properties": {"portfolio": {"type": "nothing"}}}
    _edit(plugin / ASSISTANT, lambda d: d.update(launches=[{**LAUNCH, "input_schema": broken}]))
    problems = _problems(plugin)
    assert any(
        p.startswith(f"{ASSISTANT}:launches/0/input_schema: not a valid JSON Schema") for p in problems
    )


def test_a_launch_is_declared_once(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d.update(launches=[LAUNCH, LAUNCH]))
    assert _problems(plugin) == [f"{ASSISTANT}:launches/1/name: `portfolio_review` is declared twice"]


def test_no_need_is_called_launch_beside_launches(plugin: Path) -> None:
    def change(doc: dict[str, Any]) -> None:
        doc["tools"].append(
            {
                "need": "launch",
                "operations": ["start"],
                "access": "read",
                "identity": "service",
                "required": False,
            }
        )
        doc["launches"] = [LAUNCH]

    _edit(plugin / ASSISTANT, change)
    assert _problems(plugin) == [
        f"{ASSISTANT}:tools: a need may not be called `launch` in an assistant that declares launches "
        "(their tools are `launch__<name>`)"
    ]
