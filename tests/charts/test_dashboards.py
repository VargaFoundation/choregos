"""The chart's Grafana dashboards: in English, and sortable into a folder.

On Diametral's dev tenant (2026-10-05), the six dashboards landed in the `General` folder
of the platform org, mixed with the infrastructure ones, with French titles:
nothing let the Grafana sidecar sort them. `monitoring.dashboardAnnotations` sets
the annotation the sidecar reads (usually `grafana_folder`) on each ConfigMap.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"
ACCENTS = re.compile(
    r"[àâçéèêëîïôûùüÿœ]",  # lang: fr-ok: the accents this test looks for in the dashboards
    re.IGNORECASE,
)


def _dashboards(*surcharges: str) -> list[dict]:
    command = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        command += ["--set", surcharge]
    rendu = subprocess.run(command, capture_output=True, text=True, check=True)
    return [
        doc
        for doc in yaml.safe_load_all(rendu.stdout)
        if doc
        and doc.get("kind") == "ConfigMap"
        and (doc["metadata"].get("labels") or {}).get("grafana_dashboard")
    ]


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")
def test_the_requested_annotation_is_set_on_every_dashboard() -> None:
    cms = _dashboards("monitoring.dashboardAnnotations.grafana_folder=choregos")
    assert cms
    assert all((cm["metadata"].get("annotations") or {}).get("grafana_folder") == "choregos" for cm in cms)


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")
def test_without_a_value_no_annotation() -> None:
    assert all(not cm["metadata"].get("annotations") for cm in _dashboards())


def _textes(node: object, key: str | None = None):
    if isinstance(node, dict):
        for k, v in node.items():
            if k != "expr":
                yield from _textes(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from _textes(v, key)
    elif isinstance(node, str) and key in {"title", "description", "legendFormat"}:
        yield node


@pytest.mark.parametrize("file", sorted((CHART / "dashboards").glob("*.json")), ids=lambda p: p.name)
def test_the_dashboards_are_in_english(file: pathlib.Path) -> None:
    francais = [t for t in _textes(json.loads(file.read_text())) if ACCENTS.search(t)]
    assert not francais, francais
