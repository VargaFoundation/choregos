"""The web chart sets no organisation (S42-01).

`NEXT_PUBLIC_*` variables are inlined when the image is built (`apps/web/src/lib/api.ts`), so a
runtime `NEXT_PUBLIC_DEFAULT_ORG` changes nothing -- and the chart used to set it to `varga`, which
read as a promise that a fresh deployment shows that organisation. The console shows the visitor's
last choice, else the first organisation of `GET /orgs`; the first one is named at bootstrap
(`global.bootstrap.org`).
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _env_du_web(*surcharges: str) -> dict[str, str]:
    commande = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        commande += ["--set", surcharge]
    rendu = subprocess.run(commande, capture_output=True, text=True, check=False)
    assert rendu.returncode == 0, rendu.stderr
    documents: list[dict[str, Any]] = [d for d in yaml.safe_load_all(rendu.stdout) if d]
    (web,) = [
        d for d in documents if d.get("kind") == "Deployment" and d["metadata"]["name"] == "choregos-web"
    ]
    (conteneur,) = [c for c in web["spec"]["template"]["spec"]["containers"] if c["name"] == "web"]
    return {e["name"]: e.get("value", "") for e in conteneur.get("env", []) if "value" in e}


def test_le_web_ne_fixe_aucune_organisation_par_defaut() -> None:
    env = _env_du_web()
    assert "NEXT_PUBLIC_DEFAULT_ORG" not in env
    assert "varga" not in env.values()


def test_une_valeur_inexistante_ne_revient_pas() -> None:
    assert "NEXT_PUBLIC_DEFAULT_ORG" not in _env_du_web("global.defaultOrg=diametral")
