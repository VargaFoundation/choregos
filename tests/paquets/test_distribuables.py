"""Les paquets se construisent-ils TOUS, et emportent-ils leurs données ?

La release attache les wheels. Elle a échoué deux fois de suite sur le même défaut, dans
trois paquets différents : un `pyproject.toml` déclarait à la fois `packages` (qui embarque
tout l'arbre) et un `force-include` sur un dossier DÉJÀ dedans. Hatchling refuse alors
d'ajouter deux fois un même chemin — « A second file is being added to the wheel archive at
the same path ».

Rien ne le voyait : `make ci` ne construisait aucun wheel, et l'espace de travail tourne en
mode éditable, où les fichiers de données sont simplement là. La première version de ce test
ne construisait qu'un paquet — celui qui avait cassé — et la release suivante est tombée sur
le voisin. On construit donc TOUT, et on compte : « neuf wheels » ne voulait rien dire sans
savoir combien en attendre.

Deux propriétés, et la seconde est la vraie : un paquet qui se construit mais part sans ses
gabarits de prompt ou ses presets de politique explose à l'exécution, loin d'ici. On regarde
DANS chaque wheel.

Lent (l'espace de travail entier), donc marqué `slow` : `-m "not slow"` l'écarte.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tomllib
import zipfile

import pytest

RACINE = pathlib.Path(__file__).resolve().parents[2]

#: Extensions qui sont des DONNÉES : leur absence d'un wheel est un défaut silencieux.
DONNEES = {".md", ".yaml", ".yml", ".json", ".lock", ".j2", ".sql", ".ini", ".txt"}

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("uv") is None, reason="uv absent"),
]


def _paquets() -> dict[str, pathlib.Path]:
    """{nom de distribution: dossier}, lus des pyproject de l'espace de travail."""
    trouves: dict[str, pathlib.Path] = {}
    fichiers = [*RACINE.glob("packages/*/pyproject.toml"), *RACINE.glob("apps/*/pyproject.toml")]
    for pyproject in sorted(fichiers):
        nom = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["name"]
        trouves[nom] = pyproject.parent
    return trouves


@pytest.fixture(scope="module")
def wheels(tmp_path_factory: pytest.TempPathFactory) -> dict[str, pathlib.Path]:
    """Construit TOUT l'espace de travail une fois, et rend {nom: wheel}.

    `--all-packages` plutôt qu'une boucle : c'est la commande de la release, donc c'est elle
    qu'il faut éprouver. Un échec ici nomme le paquet qui ne se construit pas.
    """
    sortie = tmp_path_factory.mktemp("dist")
    resultat = subprocess.run(
        ["uv", "build", "--all-packages", "--out-dir", str(sortie)],
        cwd=RACINE,
        capture_output=True,
        text=True,
        timeout=1800,
        # `check=False` à dessein : on veut LIRE l'erreur de hatchling et la montrer, pas
        # une trace de CalledProcessError qui cache quel paquet a refusé de se construire.
        check=False,
    )
    assert resultat.returncode == 0, f"`uv build --all-packages` a échoué :\n{resultat.stderr[-3000:]}"
    return {wheel.name.split("-")[0].replace("_", "-"): wheel for wheel in sortie.glob("*.whl")}


def test_chaque_paquet_de_l_espace_de_travail_produit_un_wheel(wheels: dict[str, pathlib.Path]) -> None:
    manquants = sorted(set(_paquets()) - set(wheels))
    assert not manquants, f"paquets sans wheel : {manquants} (obtenus : {sorted(wheels)})"


@pytest.mark.parametrize("nom", sorted(_paquets()))
def test_les_fichiers_de_donnees_partent_dans_le_wheel(nom: str, wheels: dict[str, pathlib.Path]) -> None:
    """Chaque fichier de données du paquet doit être dans son wheel, et une seule fois.

    La liste attendue est LUE sur le disque, pas écrite à la main : un gabarit ajouté demain
    est couvert sans que personne y pense.
    """
    dossier = _paquets()[nom]
    source = dossier / "src"
    modules = [chemin for chemin in sorted(source.glob("*")) if chemin.is_dir()] if source.is_dir() else []
    if not modules:
        pytest.skip(f"{nom} n'a pas de module sous src/")

    attendus = {
        str(chemin.relative_to(source))
        for module in modules
        for chemin in module.rglob("*")
        if chemin.is_file() and chemin.suffix in DONNEES and "__pycache__" not in chemin.parts
    }
    if not attendus:
        pytest.skip(f"{nom} n'embarque aucun fichier de données")

    noms = zipfile.ZipFile(wheels[nom]).namelist()
    assert len(noms) == len(set(noms)), f"{nom} : un même chemin apparaît deux fois dans le wheel"
    manquants = sorted(attendus - set(noms))
    assert not manquants, f"{nom} : données absentes du wheel : {manquants}"


def _force_include(dossier: pathlib.Path) -> dict[str, str]:
    """Les `force-include` déclarés : {chemin source: chemin dans le wheel}."""
    config = tomllib.loads((dossier / "pyproject.toml").read_text(encoding="utf-8"))
    cible = config.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("wheel", {})
    return dict(cible.get("force-include", {}))


@pytest.mark.parametrize("nom", sorted(_paquets()))
def test_les_force_include_declares_arrivent_bien_dans_le_wheel(
    nom: str, wheels: dict[str, pathlib.Path]
) -> None:
    """Un `force-include` sert quand la donnée vit HORS du dossier du module — les schémas et
    l'`openapi.yaml` des contrats, par exemple. Là il est nécessaire, et il doit donc être
    vérifié : un chemin source qui disparaît laisserait un wheel silencieusement amputé.
    """
    dossier = _paquets()[nom]
    mappages = _force_include(dossier)
    if not mappages:
        pytest.skip(f"{nom} ne déclare aucun force-include")

    noms = set(zipfile.ZipFile(wheels[nom]).namelist())
    for source, destination in sorted(mappages.items()):
        chemin = dossier / source
        assert chemin.exists(), f"{nom} : force-include sur « {source} », qui n'existe pas"
        if chemin.is_file():
            attendus = {destination}
        else:
            attendus = {
                f"{destination}/{fichier.relative_to(chemin)}"
                for fichier in chemin.rglob("*")
                if fichier.is_file() and "__pycache__" not in fichier.parts
            }
        manquants = sorted(attendus - noms)
        assert not manquants, f"{nom} : {source} → {destination}, absents du wheel : {manquants}"
