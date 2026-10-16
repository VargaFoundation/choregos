"""Do ALL the packages build, and do they carry their data?

The release attaches the wheels. It failed twice in a row on the same defect, in three
different packages: a `pyproject.toml` declared both `packages` (which embeds the whole tree)
and a `force-include` on a folder ALREADY inside it. Hatchling then refuses to add the same
path twice: "A second file is being added to the wheel archive at the same path".

Nothing saw it: `make ci` built no wheel, and the workspace runs in editable mode, where the
data files are simply there. The first version of this test built only one package, the one
that had broken, and the next release tripped over its neighbour. So we build EVERYTHING, and
we count: "nine wheels" meant nothing without knowing how many to expect.

Two properties, and the second is the real one: a package that builds but ships without its
prompt templates or its policy presets blows up at run time, far from here. We look INSIDE
each wheel.

Slow (the whole workspace), hence marked `slow`: `-m "not slow"` skips it.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tomllib
import zipfile

import pytest

RACINE = pathlib.Path(__file__).resolve().parents[2]

#: Extensions that are DATA: their absence from a wheel is a silent defect.
DONNEES = {".md", ".yaml", ".yml", ".json", ".lock", ".j2", ".sql", ".ini", ".txt", ".mako"}

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("uv") is None, reason="uv missing"),
]


def _paquets() -> dict[str, pathlib.Path]:
    """{distribution name: folder}, read from the workspace pyprojects."""
    trouves: dict[str, pathlib.Path] = {}
    fichiers = [*RACINE.glob("packages/*/pyproject.toml"), *RACINE.glob("apps/*/pyproject.toml")]
    for pyproject in sorted(fichiers):
        nom = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["name"]
        trouves[nom] = pyproject.parent
    return trouves


@pytest.fixture(scope="module")
def wheels(tmp_path_factory: pytest.TempPathFactory) -> dict[str, pathlib.Path]:
    """Build the WHOLE workspace once, and return {name: wheel}.

    `--all-packages` rather than a loop: it is the release command, so it is the one to
    exercise. A failure here names the package that does not build.
    """
    sortie = tmp_path_factory.mktemp("dist")
    resultat = subprocess.run(
        ["uv", "build", "--all-packages", "--out-dir", str(sortie)],
        cwd=RACINE,
        capture_output=True,
        text=True,
        timeout=1800,
        # `check=False` on purpose: we want to READ hatchling's error and show it, not a
        # CalledProcessError trace that hides which package refused to build.
        check=False,
    )
    assert resultat.returncode == 0, f"`uv build --all-packages` failed:\n{resultat.stderr[-3000:]}"
    return {wheel.name.split("-")[0].replace("_", "-"): wheel for wheel in sortie.glob("*.whl")}


def test_chaque_paquet_de_l_espace_de_travail_produit_un_wheel(wheels: dict[str, pathlib.Path]) -> None:
    manquants = sorted(set(_paquets()) - set(wheels))
    assert not manquants, f"packages without a wheel: {manquants} (got: {sorted(wheels)})"


@pytest.mark.parametrize("nom", sorted(_paquets()))
def test_les_fichiers_de_donnees_partent_dans_le_wheel(nom: str, wheels: dict[str, pathlib.Path]) -> None:
    """Every data file of the package must be in its wheel, exactly once.

    The expected list is READ from disk, not written by hand: a template added tomorrow is
    covered without anyone thinking about it.
    """
    dossier = _paquets()[nom]
    source = dossier / "src"
    modules = [chemin for chemin in sorted(source.glob("*")) if chemin.is_dir()] if source.is_dir() else []
    if not modules:
        pytest.skip(f"{nom} has no module under src/")

    attendus = {
        str(chemin.relative_to(source))
        for module in modules
        for chemin in module.rglob("*")
        if chemin.is_file() and chemin.suffix in DONNEES and "__pycache__" not in chemin.parts
    }
    if not attendus:
        pytest.skip(f"{nom} ships no data file")

    noms = zipfile.ZipFile(wheels[nom]).namelist()
    assert len(noms) == len(set(noms)), f"{nom}: the same path appears twice in the wheel"
    manquants = sorted(attendus - set(noms))
    assert not manquants, f"{nom}: data missing from the wheel: {manquants}"


def _force_include(dossier: pathlib.Path) -> dict[str, str]:
    """The declared `force-include`s: {source path: path in the wheel}."""
    config = tomllib.loads((dossier / "pyproject.toml").read_text(encoding="utf-8"))
    cible = config.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("wheel", {})
    return dict(cible.get("force-include", {}))


@pytest.mark.parametrize("nom", sorted(_paquets()))
def test_les_force_include_declares_arrivent_bien_dans_le_wheel(
    nom: str, wheels: dict[str, pathlib.Path]
) -> None:
    """A `force-include` is used when the data lives OUTSIDE the module folder — the contracts'
    schemas and `openapi.yaml`, for example. There it is necessary, so it must be checked:
    a source path that disappears would leave a silently truncated wheel.
    """
    dossier = _paquets()[nom]
    mappages = _force_include(dossier)
    if not mappages:
        pytest.skip(f"{nom} declares no force-include")

    noms = set(zipfile.ZipFile(wheels[nom]).namelist())
    for source, destination in sorted(mappages.items()):
        chemin = dossier / source
        assert chemin.exists(), f'{nom}: force-include on "{source}", which does not exist'
        if chemin.is_file():
            attendus = {destination}
        else:
            attendus = {
                f"{destination}/{fichier.relative_to(chemin)}"
                for fichier in chemin.rglob("*")
                if fichier.is_file() and "__pycache__" not in fichier.parts
            }
        manquants = sorted(attendus - noms)
        assert not manquants, f"{nom}: {source} → {destination}, missing from the wheel: {manquants}"


def test_les_migrations_partent_dans_le_wheel_de_l_api(wheels: dict[str, pathlib.Path]) -> None:
    """Without them, nobody migrates a database with the PUBLISHED core: they lived outside the
    package (`apps/api/migrations`), and the `choregos-api` 0.8.3 wheel carried none. An
    enterprise edition tested against the core wheels could therefore not bring up a real
    PostgreSQL.

    Every revision is counted: a forgotten migration would break the chain on upgrade."""
    source = RACINE / "apps/api/src/choregos_api/migrations"
    revisions = sorted(p.relative_to(RACINE / "apps/api/src") for p in (source / "versions").glob("*.py"))
    assert revisions, f"no revision under {source}: the test would prove nothing"
    attendus = {str(p) for p in revisions} | {
        "choregos_api/migrations/env.py",
        "choregos_api/migrations/script.py.mako",
        "choregos_api/migrer.py",
    }
    noms = set(zipfile.ZipFile(wheels["choregos-api"]).namelist())
    manquants = sorted(attendus - noms)
    assert not manquants, f"choregos-api: migrations missing from the wheel: {manquants}"
