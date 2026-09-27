# SPDX-License-Identifier: Apache-2.0
"""Une **vraie roue** d'édition entreprise porte-t-elle un point d'entrée que la plateforme trouve ?

C'est la question de fabrication du jalon E4, et elle est distincte de celle des autres tests de
greffons. `test_greffons.py` et `test_greffon_entreprise.py` écrivent un `.dist-info` **à la main** :
ils prouvent que la découverte marche, pas que la roue qu'on publierait la porte. Or ce qui casse en
silence, c'est la métadonnée — une faute dans `[project.entry-points."choregos.plugins"]` produit
une roue qui s'installe parfaitement et ne s'enregistre jamais.

DEUX CONTRAINTES DE FABRICATION, vérifiées ici parce qu'elles décident de la forme du dépôt privé :

1. **Le cœur n'est pas une dépendance déclarée.** Ses roues sont des artefacts de release GitHub,
   pas du PyPI (`release.yml` : « pas de PyPI tant que les noms ne sont pas réservés »). Une roue EE
   qui déclarerait `choregos-api==0.8.3` ferait chercher un index qui ne l'a pas. Le plan prévoit
   des images EE construites **FROM** les images CE, où les paquets du cœur sont déjà installés :
   la roue EE s'y ajoute avec `--no-deps`, et ce test construit donc une roue sans dépendances.
2. **Le point d'entrée suffit.** Aucun import du cœur au chargement du module : `brancher()` importe
   à l'appel. Une roue EE importée trop tôt casserait sur un cœur absent, dans un banc de
   construction par exemple.

Vérifié à la main le 2026-09-28 dans un environnement où le cœur est complet : la roue s'installe
avec `--no-deps`, `charger_les_greffons()` la charge, l'édition devient `enterprise`, et la
désinstallation rend `community`. Ce test garde la moitié qui peut se vérifier sans polluer
l'environnement de la suite.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import zipfile

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("uv") is None, reason="uv absent"),
]

PYPROJECT = """\
[project]
name = "choregos-ee"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
# Le cœur N'EST PAS une dépendance : l'image EE est construite FROM l'image CE.
dependencies = []

[project.entry-points."choregos.plugins"]
choregos-ee = "choregos_ee:brancher"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""

MODULE = '''\
"""Édition entreprise, en miniature : elle se déclare, et rien d'autre."""

from __future__ import annotations


def brancher() -> None:
    # L'import est DANS la fonction : une roue EE importée trop tôt casserait sur un cœur absent.
    from choregos_api.edition import ENTREPRISE, declarer

    declarer(ENTREPRISE, fonctions=frozenset({"multi_org"}))
'''


@pytest.fixture
def roue_ee(tmp_path: pathlib.Path) -> pathlib.Path:
    """Construit une vraie roue avec `uv build`, comme le ferait la CI du dépôt privé."""
    source = tmp_path / "src" / "choregos_ee"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text(MODULE, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")

    fait = subprocess.run(
        ["uv", "build", "--wheel", "-o", "dist"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert fait.returncode == 0, f"la roue ne se construit pas :\n{fait.stderr[-800:]}"
    roues = list((tmp_path / "dist").glob("*.whl"))
    assert len(roues) == 1, roues
    return roues[0]


def test_la_roue_porte_son_point_d_entree(roue_ee: pathlib.Path) -> None:
    """La métadonnée de la roue publiée, pas un `.dist-info` écrit à la main.

    C'est ce qui casse en silence : une faute dans `[project.entry-points."choregos.plugins"]`
    donne une roue qui s'installe parfaitement et ne s'enregistre jamais.
    """
    with zipfile.ZipFile(roue_ee) as archive:
        noms = archive.namelist()
        chemin = next((n for n in noms if n.endswith("entry_points.txt")), None)
        assert chemin is not None, f"aucun entry_points.txt dans la roue : {noms}"
        contenu = archive.read(chemin).decode("utf-8")

    assert "[choregos.plugins]" in contenu, contenu
    assert "choregos-ee = choregos_ee:brancher" in contenu, contenu


def test_la_roue_ne_depend_pas_du_coeur(roue_ee: pathlib.Path) -> None:
    """Déclarer le cœur ferait chercher un index qui ne l'a pas : ses roues sont des artefacts
    de release GitHub, pas du PyPI."""
    with zipfile.ZipFile(roue_ee) as archive:
        chemin = next(n for n in archive.namelist() if n.endswith("METADATA"))
        metadata = archive.read(chemin).decode("utf-8")

    requises = [ligne for ligne in metadata.splitlines() if ligne.startswith("Requires-Dist:")]
    assert not requises, f"une roue EE ne déclare pas le cœur en dépendance : {requises}"


def test_le_module_ne_touche_pas_au_coeur_a_l_import(roue_ee: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """Importable sans le cœur : un banc de construction n'a pas Choregos installé.

    On déplie la roue dans un répertoire isolé et on l'importe dans un interpréteur **sans** le
    `sys.path` de cet espace de travail — le seul moyen de prouver qu'aucun import du cœur ne part
    au chargement du module.
    """
    deplie = tmp_path / "deplie"
    with zipfile.ZipFile(roue_ee) as archive:
        archive.extractall(deplie)

    fait = subprocess.run(
        [sys.executable, "-S", "-c", "import choregos_ee; print(choregos_ee.brancher.__name__)"],
        cwd=deplie,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(deplie), "PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert fait.returncode == 0, f"le module ne s'importe pas sans le cœur :\n{fait.stderr[-600:]}"
    assert fait.stdout.strip() == "brancher"
