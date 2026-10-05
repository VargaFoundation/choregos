"""La CLI et les workflows d'un projet (ADR 0031, S16-06) : lister, publier par nom, poser une
demande dans un workflow choisi, avec ses étiquettes et ses champs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

API = "http://api.test"
ARRIVEE = """apiVersion: choregos/v1
kind: Workflow
metadata: {name: onboarding, version: 1}
actors:
  rh: {type: human, group: rh}
states:
  demande: {display: Demande, kind: wait}
  fait: {display: Fait, terminal: true}
transitions:
  - {id: t-faire, from: demande, to: fait, by: rh}
"""


@pytest.fixture(autouse=True)
def profil(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    chemin = tmp_path / "config.json"
    chemin.write_text(json.dumps({"api_url": API, "token": "chg_x", "org": "acme"}))
    monkeypatch.setenv("CHOREGOS_CONFIG", str(chemin))
    import choregos_cli.client as client_module

    monkeypatch.setattr(client_module, "CONFIG_PATH", chemin)
    return chemin


def _run(*args: str) -> Any:
    from choregos_cli.main import app

    return CliRunner().invoke(app, list(args))


@respx.mock
def test_les_workflows_d_un_projet_se_listent() -> None:
    respx.get(f"{API}/api/v1/projects/rh/workflows").mock(
        return_value=Response(
            200,
            json=[
                {"name": "onboarding", "version": 3, "is_default": True, "open_items": 2},
                {"name": "offboarding", "version": 1, "is_default": False, "open_items": 0},
            ],
        )
    )
    resultat = _run("workflow", "list", "rh")
    assert resultat.exit_code == 0, resultat.output
    assert "onboarding" in resultat.output and "v3" in resultat.output and "offboarding" in resultat.output


@respx.mock
def test_push_publie_par_le_nom_du_yaml(tmp_path: Path) -> None:
    fichier = tmp_path / "arrivee.yaml"
    fichier.write_text(ARRIVEE, encoding="utf-8")
    publie = respx.put(f"{API}/api/v1/projects/rh/workflows/onboarding").mock(
        return_value=Response(200, json={"name": "onboarding", "version": 4})
    )
    resultat = _run("workflow", "push", "rh", str(fichier), "--base-version", "3")
    assert resultat.exit_code == 0, resultat.output
    assert "onboarding v4 publié" in resultat.output
    assert json.loads(publie.calls[0].request.content) == {"yaml": ARRIVEE, "base_version": 3}


def test_push_refuse_un_yaml_sans_nom(tmp_path: Path) -> None:
    fichier = tmp_path / "sans-nom.yaml"
    fichier.write_text(ARRIVEE.replace("name: onboarding, ", ""), encoding="utf-8")
    resultat = _run("workflow", "push", "rh", str(fichier))
    assert resultat.exit_code == 1 and "metadata.name" in resultat.output


@respx.mock
def test_une_demande_choisit_son_workflow_ses_etiquettes_et_ses_champs() -> None:
    cree = respx.post(f"{API}/api/v1/projects/rh/work-items").mock(
        return_value=Response(201, json={"tracker_key": "RH-7", "state": "depart", "temporal_wf_id": "wi-7"})
    )
    resultat = _run(
        "items", "create", "rh", "--title", "Départ de Camille",
        "--workflow", "offboarding", "--label", "leaver", "--label", "cdi",
        "--field", "date_depart=2026-11-30", "--field", "materiel=[\"pc\", \"badge\"]", "--field", "jours=3",
    )  # fmt: skip
    assert resultat.exit_code == 0, resultat.output
    assert json.loads(cree.calls[0].request.content) == {
        "title": "Départ de Camille",
        "body": "",
        "size": None,
        "start": True,
        "workflow": "offboarding",
        "labels": ["leaver", "cdi"],
        "fields": {"date_depart": "2026-11-30", "materiel": ["pc", "badge"], "jours": 3},
    }


def test_un_champ_sans_egal_est_refuse() -> None:
    resultat = _run("items", "create", "rh", "--title", "x", "--field", "date_depart")
    assert resultat.exit_code == 1 and "cle=valeur" in resultat.output
