# SPDX-License-Identifier: Apache-2.0
"""L'image de l'édition entreprise, construite FROM l'image CE, tourne-t-elle en `enterprise` ?

Le plan (ADR 0024, jalon E4) construit les images EE **FROM** les images CE : le cœur y est déjà
installé, la roue EE s'ajoute avec `--no-deps` (`test_le_modele_de_paquet_ee.py` dit pourquoi elle
ne déclare pas le cœur). Le geste que tout le monde écrira est donc :

    FROM ghcr.io/vargafoundation/choregos-api:<version>
    RUN pip install --no-deps choregos_ee-*.whl

Essayé sur `choregos-api:0.8.3` le 2026-09-28 : **l'image se construit, démarre, et tourne en
`community`**. Le venv (`/app/.venv`) est créé par uv et n'a pas de `pip` ; celui du PATH est le
`pip` du système, qui pose la roue dans `~/.local` — hors du venv, donc invisible. Seule la garde
de démarrage l'attrape, et seulement si l'exploitant a écrit `global.edition: enterprise`.

L'image CE déclare donc `PIP_PYTHON` vers le python du venv : le geste évident devient le geste
juste, et le dépôt privé n'a pas à connaître la disposition interne de l'image.

Deux niveaux, parce que la CI ne peut pas faire le second : sur `main`, elle construit les images
en multi-plateforme et les pousse sans les charger.

- `test_le_socle_commun_*` lit le Dockerfile — rapide, dans `make ci`, et il rougit sans la ligne.
- `test_la_roue_ee_*` construit une vraie image FROM une image CE et exerce les deux chemins de
  chargement réels (`create_app()` de l'API, import des activités de l'orchestrateur), garde de
  démarrage comprise. Il demande Docker et `CHOREGOS_IMAGES_CE` (images séparées par des
  virgules), et s'écarte sans eux.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

import pytest

RACINE = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = RACINE / "docker" / "api.Dockerfile"
#: Les images CE à éprouver, p. ex. `choregos-api:local,choregos-orchestrator:local`.
IMAGES_CE = [i.strip() for i in os.environ.get("CHOREGOS_IMAGES_CE", "").split(",") if i.strip()]


def _etages() -> dict[str, str]:
    """{nom d'étage: son texte} pour chaque `FROM … AS nom` du Dockerfile."""
    morceaux = re.split(r"^(?=FROM\s)", DOCKERFILE.read_text(encoding="utf-8"), flags=re.M)
    etages: dict[str, str] = {}
    for morceau in morceaux:
        nom = re.match(r"FROM\s+\S+\s+AS\s+(\S+)", morceau)
        if nom:
            etages[nom.group(1)] = morceau
    return etages


def _env(etage: str) -> dict[str, str]:
    """Les `ENV clé=valeur` d'un étage, valeurs sans guillemets."""
    paires: dict[str, str] = {}
    for ligne in re.findall(r"^ENV\s+(.+)$", etage, flags=re.M):
        for cle, valeur in re.findall(r'(\w+)=("[^"]*"|\S+)', ligne):
            paires[cle] = valeur.strip('"')
    return paires


def test_les_deux_cibles_heritent_du_socle_commun() -> None:
    """Sans cette garde, une cible qui repartirait de `python:` perdrait `PIP_PYTHON` en silence."""
    etages = _etages()
    assert "commun" in etages, f"étage `commun` introuvable dans {DOCKERFILE.name} : {sorted(etages)}"
    for cible in ("api", "worker"):
        assert cible in etages, f"cible `{cible}` introuvable : {sorted(etages)}"
        assert re.match(r"FROM\s+commun\s", etages[cible]), f"la cible `{cible}` ne part pas de `commun`"


def test_le_socle_commun_fait_poser_la_roue_ee_dans_le_venv() -> None:
    """`pip install` dans une image FROM celle-ci doit viser le python qui fait tourner la plateforme."""
    env = _env(_etages()["commun"])
    chemin = env.get("PATH", "")
    venv_bin = chemin.split(":", 1)[0]
    assert venv_bin.endswith("/.venv/bin"), f"le venv n'est pas en tête du PATH : {chemin!r}"
    assert env.get("PIP_PYTHON") == f"{venv_bin}/python", (
        "le socle commun ne dit pas à `pip` d'installer dans le venv : une image EE FROM celle-ci "
        f"poserait sa roue hors du venv et tournerait en `community`. PIP_PYTHON={env.get('PIP_PYTHON')!r}"
    )


#: Ce que la plateforme fait au démarrage : l'API appelle `create_app()` (garde d'édition comprise),
#: l'orchestrateur charge les greffons en important ses activités.
VERIFICATION = """
from choregos_api.main import create_app
create_app()
import choregos_orchestrator.activities.base
from choregos_api.edition import courante, fonctions
print(courante(), ",".join(sorted(fonctions())))
"""


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker absent")
@pytest.mark.skipif(not IMAGES_CE, reason="CHOREGOS_IMAGES_CE non défini")
@pytest.mark.parametrize("image_ce", IMAGES_CE)
def test_la_roue_ee_posee_par_le_geste_evident_rend_l_edition_entreprise(
    image_ce: str, roue_ee: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    contexte = tmp_path / "contexte"
    contexte.mkdir()
    shutil.copy(roue_ee, contexte / roue_ee.name)
    # Le geste évident, et rien de plus : pas de `--python`, pas de chemin de venv.
    (contexte / "Dockerfile").write_text(
        f"FROM {image_ce}\n"
        f"COPY {roue_ee.name} /tmp/\n"
        f"RUN pip install --no-deps --no-index /tmp/{roue_ee.name}\n",
        encoding="utf-8",
    )
    etiquette = f"choregos-ee-essai:{os.getpid()}"
    construit = subprocess.run(
        ["docker", "build", "-q", "-t", etiquette, str(contexte)],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert construit.returncode == 0, f"l'image EE ne se construit pas :\n{construit.stderr[-800:]}"
    try:
        env = ["-e", "CHOREGOS_FAKES=1", "-e", "CHOREGOS_EDITION=enterprise"]
        lance = subprocess.run(
            ["docker", "run", "--rm", *env, "--entrypoint", "python", etiquette, "-c", VERIFICATION],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    finally:
        subprocess.run(["docker", "rmi", "-f", etiquette], capture_output=True, check=False)

    assert lance.returncode == 0, (
        f"FROM {image_ce} : la plateforme ne démarre pas en édition entreprise — la roue n'est pas "
        f"dans le venv qui la fait tourner :\n{lance.stderr[-600:]}"
    )
    assert lance.stdout.strip().splitlines()[-1] == "enterprise multi_org", lance.stdout
