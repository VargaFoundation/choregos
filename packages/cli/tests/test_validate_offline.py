"""`workflow validate` offline cannot know which plugins the deployment installed (ADR 0181, round C2).

A gate a plugin serves (`ci_green`, the `software-delivery` plugin's) is reported as a warning offline
when the plugin's extension is not installed beside the CLI: the deployment refuses it at publication if
it lacks the extension, and `--remote` asks it. A gate nobody serves stays an error.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

WORKFLOW = """apiVersion: choregos/v1
kind: Workflow
metadata: {name: delivery, version: 1}
actors:
  dev: {type: agent, role: implement}
states:
  todo: {display: To do, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - {id: t-do, from: todo, to: done, by: dev, gates: [{name: GATE}]}
"""


@pytest.fixture
def without_the_plugin() -> Iterator[None]:
    from tranibor_core.gates import forget

    forget("software-delivery")
    try:
        yield
    finally:
        from tranibor_software_delivery.extension import register

        register()


def _validate(tmp_path: Path, gate: str) -> tuple[int, str]:
    from tranibor_cli.main import app

    path = tmp_path / "workflow.yaml"
    path.write_text(WORKFLOW.replace("GATE", gate), encoding="utf-8")
    result = CliRunner().invoke(app, ["workflow", "validate", str(path)])
    return result.exit_code, result.output


def test_a_plugin_s_gate_is_a_warning_offline(tmp_path: Path, without_the_plugin: None) -> None:
    code, output = _validate(tmp_path, "ci_green")
    assert code == 0, output
    assert "software-delivery" in output and "checked offline" in output and "is valid" in output


def test_a_gate_nobody_serves_is_still_refused(tmp_path: Path, without_the_plugin: None) -> None:
    code, output = _validate(tmp_path, "licorne")
    assert code == 1, output
    assert "unknown gate" in output


def test_with_the_plugin_installed_nothing_is_said(tmp_path: Path) -> None:
    code, output = _validate(tmp_path, "ci_green")
    assert code == 0 and "checked offline" not in output, output
