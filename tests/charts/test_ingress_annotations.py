"""The API and console Ingresses carry the requested annotations.

On an operator's dev tenant, the NGINX Inc. controller rejected the console Ingress: the API
had taken the host, and this controller does not allow two Ingresses on the same host ("All hosts
are taken by other resources"). The console had answered 404 since its installation. This
controller's remedy is the mergeable Ingress: a master holds the host, the API and the console
declare themselves as minions -- which requires being able to annotate both.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"
KEY = "nginx\\.org/mergeable-ingress-type"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _ingress(*surcharges: str) -> dict[str, dict[str, str]]:
    command = ["helm", "template", "choregos", str(CHART)]
    for sous in ("choregos-api", "choregos-web"):
        command += ["--set", f"{sous}.ingress.kind=ingress"]
    for surcharge in surcharges:
        command += ["--set", surcharge]
    rendu = subprocess.run(command, capture_output=True, text=True, check=True)
    return {
        doc["metadata"]["name"]: doc["metadata"].get("annotations") or {}
        for doc in yaml.safe_load_all(rendu.stdout)
        if doc and doc.get("kind") == "Ingress"
    }


def test_the_api_and_the_console_carry_their_annotations() -> None:
    ingress = _ingress(
        f"choregos-api.ingress.annotations.{KEY}=minion", f"choregos-web.ingress.annotations.{KEY}=minion"
    )
    assert set(ingress) == {"choregos-api", "choregos-web"}
    assert all(a.get("nginx.org/mergeable-ingress-type") == "minion" for a in ingress.values()), ingress


def test_without_a_value_no_annotation() -> None:
    assert all(not a for a in _ingress().values())
