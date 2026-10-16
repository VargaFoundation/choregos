"""The API and console Ingresses carry the requested annotations.

On Diametral's dev tenant, the NGINX Inc. controller rejected the console Ingress: the API
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

RACINE = pathlib.Path(__file__).resolve().parents[2]
CHART = RACINE / "charts" / "choregos"
CLE = "nginx\\.org/mergeable-ingress-type"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _ingress(*surcharges: str) -> dict[str, dict[str, str]]:
    commande = ["helm", "template", "choregos", str(CHART)]
    for sous in ("choregos-api", "choregos-web"):
        commande += ["--set", f"{sous}.ingress.kind=ingress"]
    for surcharge in surcharges:
        commande += ["--set", surcharge]
    rendu = subprocess.run(commande, capture_output=True, text=True, check=True)
    return {
        doc["metadata"]["name"]: doc["metadata"].get("annotations") or {}
        for doc in yaml.safe_load_all(rendu.stdout)
        if doc and doc.get("kind") == "Ingress"
    }


def test_l_api_et_la_console_portent_leurs_annotations() -> None:
    ingress = _ingress(
        f"choregos-api.ingress.annotations.{CLE}=minion", f"choregos-web.ingress.annotations.{CLE}=minion"
    )
    assert set(ingress) == {"choregos-api", "choregos-web"}
    assert all(a.get("nginx.org/mergeable-ingress-type") == "minion" for a in ingress.values()), ingress


def test_sans_valeur_aucune_annotation() -> None:
    assert all(not a for a in _ingress().values())
