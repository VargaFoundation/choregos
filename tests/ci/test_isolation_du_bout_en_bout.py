"""The end-to-end bench does not impose its environment on the rest of a pytest session (#259).

`tests/e2e/conftest.py` set its variables in `os.environ` at import. A session that collected
`tests/e2e` together with `apps/api/tests` imposed them on EVERY test: the Tekton webhook of an
API test, which assumed an empty secret, returned 401. The CI did not see it (`norecursedirs`
keeps `tests/e2e` out of the main suite); a hand-made selection did.

Here real pytest sessions are played, in a subprocess with a cleaned environment: what is at
stake is what a session leaves behind from one directory to the next.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

RACINE = pathlib.Path(__file__).resolve().parents[2]
E2E = RACINE / "tests" / "e2e"

#: The variables the end-to-end bench sets (`ENVIRONNEMENT_E2E` in its conftest).
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
    """Another directory of the same session: it sees the original environment."""
    vues = {{nom: os.environ.get(nom) for nom in {variables!r}}}
    assert vues == {attendu!r}, f"the end-to-end bench left behind: {{vues}}"
'''


def _environnement_propre(**poses: str) -> dict[str, str]:
    """The current process environment, without any `CHOREGOS_*` variable, plus `poses`."""
    env = {nom: valeur for nom, valeur in os.environ.items() if not nom.startswith("CHOREGOS_")}
    env.update(poses)
    return env


def _pytest(*args: str, racine: pathlib.Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """A pytest session played from `racine`, with the configuration it finds there."""
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
    """The proof command of #259: `tests/e2e` collected, its scenarios dropped by `-k`.

    With the variables set at the conftest's import, the probe saw all of them.
    """
    sonde = _sonde(tmp_path / "autre", dict.fromkeys(VARIABLES_DU_BANC))
    resultat = _pytest(str(E2E), str(sonde), "-k", "sonde_259", racine=RACINE, env=_environnement_propre())
    assert resultat.returncode == 0, resultat.stdout[-3000:] + resultat.stderr[-3000:]
    assert "1 passed" in resultat.stdout, resultat.stdout[-3000:]


def test_le_banc_rend_l_environnement_apres_son_dernier_test(tmp_path: pathlib.Path) -> None:
    """The bench's real conftest, in front of a test that uses it, then another directory's probe.

    The bench's test sees its variables, except the one the environment already set, which wins.
    After it, the probe finds the original environment again: a `session`-scoped fixture would
    only tear down at the end of the session, and the probe would still see the bench's variables.
    """
    # A throwaway repository shaped like this one: `tests/e2e` (its real conftest) and
    # `apps/api/tests` under one root, in `--import-mode=importlib` like the repository's `pyproject.toml`.
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
        "    assert os.environ['CHOREGOS_ENV'] == 'deja-pose', 'a value already set wins'\n",
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
