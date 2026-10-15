"""The CI gate (`ci-ok`): the only one branch protection looks at.

It refused what it knew about — `failure`, `cancelled` — and let the rest through. On 2026-10-05,
GitHub's hosted runners stopped acquiring jobs ("not acquired by Runner of type hosted even after
multiple attempts"): `python` and `conformance-backends` came back `abandoned`, and a PR's gate was
green although no Python test had run. Here the gate's own script is played, against the results
GitHub can return.
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
    """The `ci-ok` script, played with `needs` as GitHub renders it: the exit code."""
    (etape,) = [e for e in _workflow()["jobs"]["ci-ok"]["steps"] if "run" in e]
    needs = {job: {"result": resultat, "outputs": {}} for job, resultat in resultats.items()}
    script = etape["run"].replace("${{ toJSON(needs) }}", json.dumps(needs, indent=2))
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False).returncode


def _tous(resultat: str) -> dict[str, str]:
    return dict.fromkeys(_workflow()["jobs"]["ci-ok"]["needs"], resultat)


def test_la_porte_attend_chaque_job_de_la_ci() -> None:
    """A job the gate does not wait for, it does not judge."""
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
    assert _porte(resultats) == 1, f"a `{resultat}` job got through the gate"
