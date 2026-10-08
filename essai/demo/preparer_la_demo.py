# SPDX-License-Identifier: Apache-2.0
"""Une organisation de démonstration neuve, en anglais, recréée en une commande (S21-29).

La revue produit du 2026-10-07 (point 11) : la démo du locataire dev était faite à la main, et ses
projets — `billing`, `rh` — sont nés avant que les gabarits passent en anglais (ADR 0039). Leurs
workflows et les agents RH de l'organisation `varga` restent donc en français : rien ne réécrit un
workflow publié ni un agent installé. Une organisation NEUVE naît des gabarits d'aujourd'hui.

    uv run python essai/demo/preparer_la_demo.py            # l'organisation, le projet RH, le projet de dev

Ce qu'il crée, en rejouant les scripts des deux essais :
- l'organisation `ESSAI_DEMO_ORG` (défaut `demo`) — l'appelant en devient administrateur ; en édition
  communautaire, qui n'en tient qu'une, la démo va dans l'organisation existante, projet `rh-demo` ;
- le projet `rh` (gabarit `joiners-leavers`, ses cinq connecteurs vers les faux servis) —
  `essai/rh/scenario_rh.py preparer` ;
- le projet `dev` (gabarit `github-software-delivery`) — `essai/projet-dev/scenario_dev.py preparer`,
  seulement si `ESSAI_INSTALLATION` est posée : il lui faut l'App GitHub du locataire.

Variables : celles des deux essais (`CHOREGOS_DEV_URL`, `CHOREGOS_DEV_TOKEN`…) ; `CHOREGOS_DEV_ORG`
est posée ici, à partir de `ESSAI_DEMO_ORG`.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
from types import ModuleType

ICI = pathlib.Path(__file__).resolve().parent


def _essai(chemin: str, nom: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(nom, ICI.parent / chemin)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    org = os.environ.get("ESSAI_DEMO_ORG", "demo")
    os.environ["CHOREGOS_DEV_ORG"] = org
    rh = _essai("rh/scenario_rh.py", "scenario_rh")
    with rh.client() as http:
        connues = {o["slug"] for o in rh.lire(http.get("/orgs"), 200)}
        if org not in connues:
            reponse = http.post("/orgs", json={"slug": org, "name": org.capitalize()})
            if reponse.status_code == 409 and len(connues) == 1:
                # L'édition communautaire tient une seule organisation (ADR 0024) : la démo s'y range,
                # sous un projet à elle (`ESSAI_RH_PROJET`, défaut `rh-demo`).
                org = next(iter(connues))
                os.environ["CHOREGOS_DEV_ORG"] = org
                os.environ.setdefault("ESSAI_RH_PROJET", "rh-demo")
                rh = _essai("rh/scenario_rh.py", "scenario_rh")
                print(f"organisation : une seule en édition communautaire — la démo va dans {org}")
            else:
                rh.lire(reponse, 201)
                print(f"organisation {org} : créée")
        else:
            print(f"organisation {org} : déjà là")
    rh.preparer()
    if os.environ.get("ESSAI_INSTALLATION"):
        _essai("projet-dev/scenario_dev.py", "scenario_dev").preparer()
    else:
        print("projet dev : pas créé — ESSAI_INSTALLATION (l'App GitHub du locataire) n'est pas posée")


if __name__ == "__main__":
    sys.exit(main())
