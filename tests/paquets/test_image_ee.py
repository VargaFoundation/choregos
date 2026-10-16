# SPDX-License-Identifier: Apache-2.0
"""Does the enterprise-edition image, built FROM the CE image, run as `enterprise`?

The plan (ADR 0024, milestone E4) builds the EE images **FROM** the CE images: the core is
already installed, and the EE wheel is added with `--no-deps` (`test_le_modele_de_paquet_ee.py`
says why it does not declare the core). The gesture everybody will write is therefore:

    FROM ghcr.io/vargafoundation/choregos-api:<version>
    RUN pip install --no-deps choregos_ee-*.whl

Tried on `choregos-api:0.8.3` on 2026-09-28: **the image builds, starts, and runs as
`community`**. The venv (`/app/.venv`) is created by uv and has no `pip`; the one on the PATH is
the system `pip`, which puts the wheel in `~/.local` — outside the venv, hence invisible. Only
the startup guard catches it, and only if the operator wrote `global.edition: enterprise`.

The CE image therefore points `PIP_PYTHON` at the venv's python: the obvious gesture becomes the
right one, and the private repository need not know the image's internal layout.

Two levels, because the CI cannot do the second: on `main`, it builds the images multi-platform
and pushes them without loading them.

- `test_le_socle_commun_*` reads the Dockerfile — fast, in `make ci`, and it goes red without
  the line.
- `test_la_roue_ee_*` builds a real image FROM a CE image and exercises the two real loading
  paths (the API's `create_app()`, the import of the orchestrator's activities), startup guard
  included. It needs Docker and `CHOREGOS_IMAGES_CE` (comma-separated images), and skips
  without them.
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
#: The CE images to exercise, e.g. `choregos-api:local,choregos-orchestrator:local`.
IMAGES_CE = [i.strip() for i in os.environ.get("CHOREGOS_IMAGES_CE", "").split(",") if i.strip()]


def _etages() -> dict[str, str]:
    """{stage name: its text} for each `FROM … AS name` of the Dockerfile."""
    morceaux = re.split(r"^(?=FROM\s)", DOCKERFILE.read_text(encoding="utf-8"), flags=re.M)
    etages: dict[str, str] = {}
    for morceau in morceaux:
        nom = re.match(r"FROM\s+\S+\s+AS\s+(\S+)", morceau)
        if nom:
            etages[nom.group(1)] = morceau
    return etages


def _env(etage: str) -> dict[str, str]:
    """The `ENV key=value`s of a stage, values without quotes."""
    paires: dict[str, str] = {}
    for ligne in re.findall(r"^ENV\s+(.+)$", etage, flags=re.M):
        for cle, valeur in re.findall(r'(\w+)=("[^"]*"|\S+)', ligne):
            paires[cle] = valeur.strip('"')
    return paires


def test_les_deux_cibles_heritent_du_socle_commun() -> None:
    """Without this guard, a target restarting from `python:` would silently lose `PIP_PYTHON`."""
    etages = _etages()
    assert "commun" in etages, f"stage `commun` not found in {DOCKERFILE.name}: {sorted(etages)}"
    for cible in ("api", "worker"):
        assert cible in etages, f"target `{cible}` not found: {sorted(etages)}"
        assert re.match(r"FROM\s+commun\s", etages[cible]), f"target `{cible}` does not start from `commun`"


def test_le_socle_commun_fait_poser_la_roue_ee_dans_le_venv() -> None:
    """`pip install` in an image FROM this one must target the python that runs the platform."""
    env = _env(_etages()["commun"])
    chemin = env.get("PATH", "")
    venv_bin = chemin.split(":", 1)[0]
    assert venv_bin.endswith("/.venv/bin"), f"the venv is not first on the PATH: {chemin!r}"
    assert env.get("PIP_PYTHON") == f"{venv_bin}/python", (
        "the common base does not tell `pip` to install into the venv: an EE image FROM this one "
        f"would put its wheel outside the venv and run as `community`. PIP_PYTHON={env.get('PIP_PYTHON')!r}"
    )


#: What the platform does at startup: the API calls `create_app()` (edition guard included),
#: the orchestrator loads the plugins by importing its activities.
VERIFICATION = """
from choregos_api.main import create_app
create_app()
import choregos_orchestrator.activities.base
from choregos_api.edition import courante, fonctions
print(courante(), ",".join(sorted(fonctions())))
"""


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker missing")
@pytest.mark.skipif(not IMAGES_CE, reason="CHOREGOS_IMAGES_CE not set")
@pytest.mark.parametrize("image_ce", IMAGES_CE)
def test_la_roue_ee_posee_par_le_geste_evident_rend_l_edition_entreprise(
    image_ce: str, roue_ee: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    contexte = tmp_path / "contexte"
    contexte.mkdir()
    shutil.copy(roue_ee, contexte / roue_ee.name)
    # The obvious gesture, and nothing more: no `--python`, no venv path.
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
    assert construit.returncode == 0, f"the EE image does not build:\n{construit.stderr[-800:]}"
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
        f"FROM {image_ce}: the platform does not start in enterprise edition — the wheel is not "
        f"in the venv that runs it:\n{lance.stderr[-600:]}"
    )
    assert lance.stdout.strip().splitlines()[-1] == "enterprise multi_org", lance.stdout
