"""The CLI and a project's workflows (ADR 0031, S16-06): list, publish by name, file a request in a
chosen workflow, with its labels and its fields."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

API = "http://api.test"
ONBOARDING = """apiVersion: choregos/v1
kind: Workflow
metadata: {name: onboarding, version: 1}
actors:
  hr: {type: human, group: hr}
states:
  requested: {display: Requested, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - {id: t-do, from: requested, to: done, by: hr}
"""


@pytest.fixture(autouse=True)
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"api_url": API, "token": "chg_x", "org": "acme"}))
    monkeypatch.setenv("CHOREGOS_CONFIG", str(path))
    import choregos_cli.client as client_module

    monkeypatch.setattr(client_module, "CONFIG_PATH", path)
    return path


def _run(*args: str) -> Any:
    from choregos_cli.main import app

    return CliRunner().invoke(app, list(args))


@respx.mock
def test_a_projects_workflows_are_listed() -> None:
    respx.get(f"{API}/api/v1/projects/hr/workflows").mock(
        return_value=Response(
            200,
            json=[
                {"name": "onboarding", "version": 3, "is_default": True, "open_items": 2},
                {"name": "offboarding", "version": 1, "is_default": False, "open_items": 0},
            ],
        )
    )
    result = _run("workflow", "list", "hr")
    assert result.exit_code == 0, result.output
    assert "onboarding" in result.output and "v3" in result.output and "offboarding" in result.output


@respx.mock
def test_push_publishes_under_the_yamls_name(tmp_path: Path) -> None:
    file = tmp_path / "onboarding.yaml"
    file.write_text(ONBOARDING, encoding="utf-8")
    published = respx.put(f"{API}/api/v1/projects/hr/workflows/onboarding").mock(
        return_value=Response(200, json={"name": "onboarding", "version": 4})
    )
    result = _run("workflow", "push", "hr", str(file), "--base-version", "3")
    assert result.exit_code == 0, result.output
    assert "onboarding v4 published" in result.output
    assert json.loads(published.calls[0].request.content) == {"yaml": ONBOARDING, "base_version": 3}


def test_push_refuses_a_yaml_without_a_name(tmp_path: Path) -> None:
    file = tmp_path / "no-name.yaml"
    file.write_text(ONBOARDING.replace("name: onboarding, ", ""), encoding="utf-8")
    result = _run("workflow", "push", "hr", str(file))
    assert result.exit_code == 1 and "metadata.name" in result.output


@respx.mock
def test_a_request_chooses_its_workflow_its_labels_and_its_fields() -> None:
    created = respx.post(f"{API}/api/v1/projects/hr/work-items").mock(
        return_value=Response(201, json={"tracker_key": "HR-7", "state": "leaving", "temporal_wf_id": "wi-7"})
    )
    result = _run(
        "items", "create", "hr", "--title", "Camille leaves",
        "--workflow", "offboarding", "--label", "leaver", "--label", "permanent",
        "--field", "last_day=2026-11-30", "--field", "devices=[\"laptop\", \"badge\"]", "--field", "days=3",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert json.loads(created.calls[0].request.content) == {
        "title": "Camille leaves",
        "body": "",
        "size": None,
        "start": True,
        "workflow": "offboarding",
        "labels": ["leaver", "permanent"],
        "fields": {"last_day": "2026-11-30", "devices": ["laptop", "badge"], "days": 3},
    }


def test_a_field_without_an_equals_sign_is_refused() -> None:
    result = _run("items", "create", "hr", "--title", "x", "--field", "last_day")
    assert result.exit_code == 1 and "key=value" in result.output
