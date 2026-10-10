"""Le banc de bout en bout n'impose pas son environnement au reste d'une session pytest (#259).

`tests/e2e/conftest.py` posait ses variables dans `os.environ` à l'import. Une session qui
collectait `tests/e2e` avec `apps/api/tests` les imposait à TOUS les tests : le webhook Tekton
d'un test de l'API, qui supposait un secret vide, rendait 401. La CI ne le voyait pas
(`norecursedirs` écarte `tests/e2e` de la suite principale) ; une sélection faite à la main, si.

On joue ici de vraies sessions pytest, dans un sous-processus à l'environnement nettoyé : ce qui
est en cause, c'est ce qu'une session laisse d'un répertoire à l'autre.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

RACINE = pathlib.Path(__file__).resolve().parents[2]
E2E = RACINE / "tests" / "e2e"

#: Les variables que pose le banc de bout en bout (`ENVIRONNEMENT_E2E` de son conftest).
VARIABLES_DU_BANC = (
    "CHOREGOS_FAKES",
    "CHOREGOS_ENV",
    "CHOREGOS_DEV_LOGIN_ENABLED",
    "CHOREGOS_DEV_ADMIN_EMAILS",
    "CHOREGOS_GENERIC_WEBHOOK_SECRET",
)

SONDE = '''
import os

def test_sonde_259() -> None:
    """Un autre répertoire de la même session : il voit l'environnement d'origine."""
    vues = {{nom: os.environ.get(nom) for nom in {variables!r}}}
    assert vues == {attendu!r}, f"le banc de bout en bout a laissé : {{vues}}"
'''


def _environnement_propre(**poses: str) -> dict[str, str]:
    """L'environnement du processus courant, sans aucune variable `CHOREGOS_*`, plus `poses`."""
    env = {nom: valeur for nom, valeur in os.environ.items() if not nom.startswith("CHOREGOS_")}
    env.update(poses)
    return env


def _pytest(*args: str, racine: pathlib.Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Une session pytest jouée depuis `racine`, avec la configuration qu'elle y trouve."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=racine,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def _sonde(dossier: pathlib.Path, attendu: dict[str, str | None]) -> pathlib.Path:
    dossier.mkdir(parents=True)
    fichier = dossier / "test_sonde_259.py"
    fichier.write_text(SONDE.format(variables=VARIABLES_DU_BANC, attendu=attendu), encoding="utf-8")
    return fichier


def test_collecter_le_bout_en_bout_ne_touche_pas_l_environnement_d_un_autre_repertoire(
    tmp_path: pathlib.Path,
) -> None:
    """La commande de preuve de #259 : `tests/e2e` collecté, ses scénarios écartés par `-k`.

    Avec les variables posées à l'import du conftest, la sonde les voyait toutes.
    """
    sonde = _sonde(tmp_path / "autre", dict.fromkeys(VARIABLES_DU_BANC))
    resultat = _pytest(str(E2E), str(sonde), "-k", "sonde_259", racine=RACINE, env=_environnement_propre())
    assert resultat.returncode == 0, resultat.stdout[-3000:] + resultat.stderr[-3000:]
    assert "1 passed" in resultat.stdout, resultat.stdout[-3000:]


def test_le_banc_rend_l_environnement_apres_son_dernier_test(tmp_path: pathlib.Path) -> None:
    """Le conftest réel du banc, devant un test qui s'en sert, puis la sonde d'un autre répertoire.

    Le test du banc voit ses variables — sauf celle que l'environnement posait déjà, qui l'emporte.
    Après lui, la sonde retrouve l'environnement d'origine : une fixture de portée `session` ne se
    démonterait qu'à la fin de la session, et la sonde verrait encore les variables du banc.
    """
    # Un dépôt jetable à l'image de celui-ci : `tests/e2e` (son conftest réel) et `apps/api/tests`
    # sous une même racine, en `--import-mode=importlib` comme le `pyproject.toml` du dépôt.
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --import-mode=importlib\n", encoding="utf-8")
    banc = tmp_path / "tests" / "e2e"
    banc.mkdir(parents=True)
    shutil.copy(E2E / "__init__.py", banc / "__init__.py")
    shutil.copy(E2E / "conftest.py", banc / "conftest.py")
    (banc / "test_banc_259.py").write_text(
        "import os\n\n"
        "def test_banc_259() -> None:\n"
        "    assert os.environ['CHOREGOS_GENERIC_WEBHOOK_SECRET'] == 'e2e-webhook-secret'\n"
        "    assert os.environ['CHOREGOS_DEV_LOGIN_ENABLED'] == 'true'\n"
        "    assert os.environ['CHOREGOS_ENV'] == 'deja-pose', 'une valeur déjà posée l’emporte'\n",
        encoding="utf-8",
    )
    attendu: dict[str, str | None] = dict.fromkeys(VARIABLES_DU_BANC)
    attendu["CHOREGOS_ENV"] = "deja-pose"
    sonde = _sonde(tmp_path / "apps" / "api" / "tests", attendu)
    resultat = _pytest(
        "tests/e2e",
        str(sonde.relative_to(tmp_path)),
        racine=tmp_path,
        env=_environnement_propre(CHOREGOS_ENV="deja-pose"),
    )
    assert resultat.returncode == 0, resultat.stdout[-3000:] + resultat.stderr[-3000:]
    assert "2 passed" in resultat.stdout, resultat.stdout[-3000:]
