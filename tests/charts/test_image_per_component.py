"""One image per component (S17-03): the enterprise edition serves its API and its orchestrator from
a private project; the console, the database, Temporal and the rest come from the core.

The chart only knew how to compose one registry: `global.imageRegistry` for all images. Serving
the `-ee` images therefore meant serving everything from the private project -- or forking the chart.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml
from choregos_core.publishing import PUBLISHING

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"
PRIVE = "harbor.build.diametral.com/choregos-ee"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _images(*surcharges: str) -> dict[str, list[str]]:
    command = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        command += ["--set", surcharge]
    output = subprocess.run(command, capture_output=True, text=True, check=True, timeout=120)
    images: dict[str, list[str]] = {}
    for doc in yaml.safe_load_all(output.stdout):
        if not doc or doc.get("kind") not in {"Deployment", "StatefulSet", "Job"}:
            continue
        spec: dict[str, Any] = doc["spec"]["template"]["spec"]
        conteneurs = spec.get("initContainers", []) + spec.get("containers", [])
        images[f"{doc['kind']}/{doc['metadata']['name']}"] = [c["image"] for c in conteneurs]
    return images


EE = (
    f"choregos-api.image.registry={PRIVE}",
    "choregos-api.image.repository=choregos-api-ee",
    f"choregos-orchestrator.image.registry={PRIVE}",
    "choregos-orchestrator.image.repository=choregos-orchestrator-ee",
)


def test_the_api_and_the_orchestrator_come_from_the_private_project_the_rest_of_the_core() -> None:
    images = _images(*EE)
    prives = {name for name, refs in images.items() if any(r.startswith(PRIVE) for r in refs)}
    assert any("api" in name for name in prives) and any("orchestrator" in name for name in prives), prives
    # The migration job runs the API image: it follows its registry.
    assert any(name.startswith("Job/") for name in prives), prives
    for name, refs in images.items():
        if name not in prives:
            assert all(not r.startswith(PRIVE) for r in refs), (name, refs)
    assert any(r.startswith(PUBLISHING.image("choregos-web")) for refs in images.values() for r in refs)


def test_the_global_digest_does_not_point_at_an_image_of_another_registry() -> None:
    images = _images(*EE, "global.imageDigest=sha256:" + "a" * 64)
    for name, refs in images.items():
        for ref in refs:
            if ref.startswith(PRIVE):
                assert "@sha256" not in ref, f"{name}: the core digest set on an EE image"
            elif ref.startswith(PUBLISHING.image("choregos-")):
                assert "@sha256:" in ref, f"{name}: the core keeps its digest"


def test_without_an_override_nothing_changes() -> None:
    for name, refs in _images().items():
        assert all(not r.startswith(PRIVE) for r in refs), name
