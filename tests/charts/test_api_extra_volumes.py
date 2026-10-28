"""The API takes extra volumes and mounts from the values, and refuses a name the chart owns."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is missing")

VOLUMES = '{"choregos-api": {"extraVolumes": [{"name": "theme", "configMap": {"name": "my-theme"}}]}}'


def _helm(values: str | None, tmp_path: pathlib.Path) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART)]
    if values is not None:
        path = tmp_path / "values.yaml"
        path.write_text(yaml.safe_dump(yaml.safe_load(values)))
        command += ["-f", str(path)]
    return subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)


def _api(output: str) -> dict[str, Any]:
    for doc in yaml.safe_load_all(output):
        if doc and doc.get("kind") == "Deployment" and doc["metadata"]["name"] == "choregos-api":
            spec: dict[str, Any] = doc["spec"]["template"]["spec"]
            return spec
    raise AssertionError("no choregos-api Deployment rendered")


def test_default_adds_nothing(tmp_path: pathlib.Path) -> None:
    spec = _api(_helm(None, tmp_path).stdout)
    assert {v["name"] for v in spec["volumes"]} <= {
        "tmp",
        "catalogue",
        "workflow-templates",
        "playbooks",
        # The built-in agents' plugin configuration, on by default since S43-09 (ADR 0093 §6).
        "plugins-config",
    }
    assert "theme" not in {m["name"] for m in spec["containers"][0]["volumeMounts"]}


def test_extra_volumes_and_mounts_are_rendered(tmp_path: pathlib.Path) -> None:
    values = (
        '{"choregos-api": {"extraVolumes": [{"name": "theme", "configMap": {"name": "my-theme"}}],'
        ' "extraVolumeMounts": [{"name": "theme", "mountPath": "/etc/theme", "readOnly": true}]}}'
    )
    result = _helm(values, tmp_path)
    assert result.returncode == 0, result.stderr
    spec = _api(result.stdout)
    assert {"name": "theme", "configMap": {"name": "my-theme"}} in spec["volumes"]
    assert {"name": "theme", "mountPath": "/etc/theme", "readOnly": True} in spec["containers"][0][
        "volumeMounts"
    ]
    # The chart's own volumes are still there.
    assert "tmp" in {v["name"] for v in spec["volumes"]}


@pytest.mark.parametrize("name", ["tmp", "catalogue", "workflow-templates", "playbooks"])
def test_a_name_the_chart_owns_fails_the_render(tmp_path: pathlib.Path, name: str) -> None:
    values = VOLUMES.replace('"theme"', f'"{name}"')
    result = _helm(values, tmp_path)
    assert result.returncode != 0
    assert "is a volume of the chart" in result.stderr
