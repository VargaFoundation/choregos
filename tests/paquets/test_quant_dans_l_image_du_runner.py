"""The runner image carries `choregos-quant`: a job stage runs it offline (S38-06)."""

from __future__ import annotations

import pathlib
import tomllib

RACINE = pathlib.Path(__file__).resolve().parents[2]


def test_the_runner_package_depends_on_quant() -> None:
    """`uv sync --package choregos-runner` installs only what the runner declares."""
    data = tomllib.loads((RACINE / "packages/runner/pyproject.toml").read_text(encoding="utf-8"))
    assert "choregos-quant" in data["project"]["dependencies"]


def test_the_runner_dockerfile_checks_that_quant_starts() -> None:
    texte = (RACINE / "docker/runner.Dockerfile").read_text(encoding="utf-8")
    assert "choregos-quant --help" in texte


def test_quant_has_no_third_party_dependency() -> None:
    data = tomllib.loads((RACINE / "packages/quant/pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["dependencies"] == []
