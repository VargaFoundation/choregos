"""Tests of the runner CLI (`choregos-runner`)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from choregos_runner.cli import app
from choregos_runner.exits import Exit
from typer.testing import CliRunner

cli = CliRunner()


def test_the_backends_list_shows_the_pinned_versions() -> None:
    result = cli.invoke(app, ["backends"])
    assert result.exit_code == 0
    assert "claude-code" in result.stdout and "gemini-cli" in result.stdout
    assert "openhands" not in result.stdout, "a retired backend is no longer offered"


def test_validate_accepts_a_conforming_result(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(
            {
                "schema": "choregos/StageResult/v1",
                "status": "done",
                "summary": "step finished",
                "evidence": {"tests_passed": True, "tests_run": 3},
            }
        ),
        encoding="utf-8",
    )
    result = cli.invoke(app, ["validate", str(path)])
    assert result.exit_code == 0
    assert "valid — status=done" in result.stdout


def test_validate_refuses_an_invalid_result(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text('{"status": "licorne"}', encoding="utf-8")
    result = cli.invoke(app, ["validate", str(path)])
    assert result.exit_code == int(Exit.INVALID_RESULT)


def test_validate_reports_a_missing_file(tmp_path: Path) -> None:
    result = cli.invoke(app, ["validate", str(tmp_path / "rien.json")])
    assert result.exit_code == int(Exit.INVALID_RESULT)


def test_the_cli_starts_without_the_api_dependencies() -> None:
    """The runner image installs `choregos-runner` alone: no SQLAlchemy, no FastAPI.

    `docker/runner.Dockerfile` runs `choregos-runner --help` and the build failed on main
    after #409, because importing `choregos_adapters` imported SQLAlchemy. Blocking those
    modules here reproduces that image in the test suite.
    """
    code = (
        "import sys\n"
        "for name in ('sqlalchemy', 'fastapi', 'asyncpg', 'alembic'):\n"
        "    sys.modules[name] = None\n"
        "from choregos_runner.cli import app\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
