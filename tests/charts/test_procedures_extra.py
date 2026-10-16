"""`api.proceduresExtra` mounts a ConfigMap of site notes and points the API at it (ADR 0067)."""

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


def _api(values: dict[str, Any] | None, tmp_path: pathlib.Path) -> dict[str, Any]:
    command = ["helm", "template", "choregos", str(CHART)]
    if values is not None:
        path = tmp_path / "values.yaml"
        path.write_text(yaml.safe_dump(values))
        command += ["-f", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr
    for doc in yaml.safe_load_all(result.stdout):
        if doc and doc.get("kind") == "Deployment" and doc["metadata"]["name"] == "choregos-api":
            spec: dict[str, Any] = doc["spec"]["template"]["spec"]
            return spec
    raise AssertionError("no choregos-api Deployment rendered")


def _env(spec: dict[str, Any]) -> dict[str, str]:
    return {e["name"]: e.get("value", "") for e in spec["containers"][0]["env"]}


def test_by_default_nothing_is_mounted_and_the_variable_is_absent(tmp_path: pathlib.Path) -> None:
    spec = _api(None, tmp_path)
    assert "CHOREGOS_PROCEDURES_EXTRA" not in _env(spec)
    assert "procedures-extra" not in {v["name"] for v in spec["volumes"]}


def test_a_config_map_is_mounted_read_only_and_named_to_the_api(tmp_path: pathlib.Path) -> None:
    spec = _api({"choregos-api": {"proceduresExtra": "choregos-procedures"}}, tmp_path)
    assert _env(spec)["CHOREGOS_PROCEDURES_EXTRA"] == "/etc/choregos/procedures"
    assert {"name": "procedures-extra", "configMap": {"name": "choregos-procedures"}} in spec["volumes"]
    assert {
        "name": "procedures-extra",
        "mountPath": "/etc/choregos/procedures",
        "readOnly": True,
    } in spec["containers"][0]["volumeMounts"]
