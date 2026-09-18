"""Tests de la CLI du runner (`choregos-runner`)."""

from __future__ import annotations

import json
from pathlib import Path

from choregos_runner.cli import app
from choregos_runner.exits import Exit
from typer.testing import CliRunner

cli = CliRunner()


def test_la_liste_des_backends_affiche_les_versions_epinglees() -> None:
    result = cli.invoke(app, ["backends"])
    assert result.exit_code == 0
    assert "claude-code" in result.stdout and "openhands" in result.stdout


def test_validate_accepte_un_resultat_conforme(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(
            {
                "schema": "choregos/StageResult/v1",
                "status": "done",
                "summary": "étape terminée",
                "evidence": {"tests_passed": True, "tests_run": 3},
            }
        ),
        encoding="utf-8",
    )
    result = cli.invoke(app, ["validate", str(path)])
    assert result.exit_code == 0
    assert "valide" in result.stdout


def test_validate_refuse_un_resultat_invalide(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text('{"status": "licorne"}', encoding="utf-8")
    result = cli.invoke(app, ["validate", str(path)])
    assert result.exit_code == int(Exit.INVALID_RESULT)


def test_validate_signale_un_fichier_absent(tmp_path: Path) -> None:
    result = cli.invoke(app, ["validate", str(tmp_path / "rien.json")])
    assert result.exit_code == int(Exit.INVALID_RESULT)
