"""La porte de la CI (`ci-ok`) : la seule que la protection de branche regarde.

Elle refusait ce qu'elle connaissait — `failure`, `cancelled` — et laissait passer le reste. Le
2026-10-05, les runners hébergés de GitHub n'acquéraient plus les jobs (« not acquired by Runner of
type hosted even after multiple attempts ») : `python` et `conformance-backends` sont revenus
`abandoned`, et la porte d'une PR était verte sans qu'un test Python ait tourné. On joue ici le
script de la porte lui-même, contre les résultats que GitHub peut rendre.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest
import yaml

CI = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _porte(resultats: dict[str, str]) -> int:
    """Le script de `ci-ok`, joué avec `needs` tel que GitHub le rend : le code de sortie."""
    (etape,) = [e for e in _workflow()["jobs"]["ci-ok"]["steps"] if "run" in e]
    needs = {job: {"result": resultat, "outputs": {}} for job, resultat in resultats.items()}
    script = etape["run"].replace("${{ toJSON(needs) }}", json.dumps(needs, indent=2))
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False).returncode


def _tous(resultat: str) -> dict[str, str]:
    return dict.fromkeys(_workflow()["jobs"]["ci-ok"]["needs"], resultat)


def test_la_porte_attend_chaque_job_de_la_ci() -> None:
    """Un job que la porte n'attend pas, elle ne le juge pas."""
    jobs = set(_workflow()["jobs"]) - {"ci-ok"}
    assert set(_workflow()["jobs"]["ci-ok"]["needs"]) == jobs


def test_reussis_ou_sautes_la_porte_s_ouvre() -> None:
    resultats = _tous("success")
    resultats["charts"] = "skipped"
    assert _porte(resultats) == 0


@pytest.mark.parametrize("resultat", ["failure", "cancelled", "abandoned", "timed_out", "quelque_chose"])
def test_tout_autre_resultat_ferme_la_porte(resultat: str) -> None:
    resultats = _tous("success")
    resultats["python"] = resultat
    assert _porte(resultats) == 1, f"un job `{resultat}` passait la porte"
