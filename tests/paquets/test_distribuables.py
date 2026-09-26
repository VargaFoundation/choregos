"""Les neuf paquets se construisent-ils, et emportent-ils leurs données ?

La release attache les wheels : depuis la v0.4.0 elle échouait sur un seul paquet,
`choregos-playbooks`, dont le `pyproject.toml` déclarait à la fois `packages` (qui embarque
tout l'arbre) et un `force-include` sur `roles/` — hatchling refuse alors d'ajouter deux
fois un même chemin, « A second file is being added to the wheel archive at the same path ».
Rien ne le voyait : `make ci` ne construit pas de wheel, et l'espace de travail marche en
mode éditable, où les gabarits sont simplement là.

Deux propriétés valent d'être tenues, et la seconde est la vraie : un paquet qui se
construit mais part sans ses gabarits de prompt est un paquet qui explose à l'exécution,
loin d'ici. On regarde donc DANS le wheel.

Lent (une construction réelle), donc marqué `slow` : `-m "not slow"` l'écarte.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import zipfile

import pytest

RACINE = pathlib.Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("uv") is None, reason="uv absent"),
]


def _construire(paquet: str, sortie: pathlib.Path) -> pathlib.Path:
    subprocess.run(
        ["uv", "build", "--package", paquet, "--out-dir", str(sortie)],
        cwd=RACINE,
        capture_output=True,
        text=True,
        check=True,
        timeout=600,
    )
    wheels = sorted(sortie.glob("*.whl"))
    assert len(wheels) == 1, f"{paquet} : un wheel attendu, {len(wheels)} trouvé(s)"
    return wheels[0]


def test_les_gabarits_de_playbook_partent_dans_le_wheel(tmp_path: pathlib.Path) -> None:
    """Les prompts par rôle sont des `.md` à côté du code : sans eux, le runner n'a rien à
    donner à l'agent. Et chaque chemin ne doit y être qu'UNE fois."""
    wheel = _construire("choregos-playbooks", tmp_path)
    noms = zipfile.ZipFile(wheel).namelist()

    assert len(noms) == len(set(noms)), "un même chemin apparaît deux fois dans le wheel"
    roles = [n for n in noms if n.startswith("choregos_playbooks/roles/")]
    assert any(n.endswith("/_base.md") for n in roles), f"le gabarit de base manque : {roles}"
    # Un gabarit par rôle du DSL, au moins : refine, plan, implement, review, verify.
    prompts = [n for n in roles if n.endswith("/prompt.md")]
    assert len(prompts) >= 5, f"gabarits de rôle trouvés : {prompts}"
    assert [n for n in noms if n.startswith("choregos_playbooks/evals/")], "les évals manquent"


def test_les_gabarits_du_dossier_source_sont_tous_dans_le_wheel(tmp_path: pathlib.Path) -> None:
    """Aucun `.md` du paquet ne doit rester sur le carreau : c'est la comparaison qui le dit,
    pas une liste écrite à la main qui vieillirait sans prévenir."""
    source = RACINE / "packages" / "playbooks" / "src" / "choregos_playbooks"
    attendus = {str(chemin.relative_to(source.parent)) for chemin in source.rglob("*.md") if chemin.is_file()}
    noms = set(zipfile.ZipFile(_construire("choregos-playbooks", tmp_path)).namelist())
    manquants = sorted(attendus - noms)
    assert not manquants, f"gabarits absents du wheel : {manquants}"
