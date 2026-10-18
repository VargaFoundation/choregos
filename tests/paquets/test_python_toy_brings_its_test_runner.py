"""Can a live eval case's tests run on a Choregos runner? (S37-14)

The runner's `python3` is its own virtual environment, without pytest. The first live matrix on dev
(`evals-live-20261018`) asserted `python -m pytest -q` on five tickets: the fact was false whatever
the agent did, so every Python case failed in every context. The toy repository now picks an
interpreter that has pytest, and makes a `.venv` with it on first use when the one present has none;
the tickets assert `make test`.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOY = ROOT / "tests/fixtures/repos/python-toy"
TICKETS = ROOT / "tests/fixtures/tickets"

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def _planned(tmp_path: pathlib.Path, python3_has_pytest: bool) -> str:
    """What `make test` would run, with a `python3` that has pytest or not (`make -n` runs nothing
    but the `$(shell …)` probe, answered by a stub)."""
    work = tmp_path / "toy"
    shutil.copytree(TOY, work, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "python3"
    stub.write_text(f"#!/bin/sh\nexit {0 if python3_has_pytest else 1}\n", encoding="utf-8")
    stub.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    done = subprocess.run(
        ["make", "-n", "test"], cwd=work, env=env, capture_output=True, text=True, check=True
    )
    return done.stdout


def test_without_pytest_the_toy_makes_its_own_venv(tmp_path: pathlib.Path) -> None:
    planned = _planned(tmp_path, python3_has_pytest=False)
    assert "python3 -m venv .venv" in planned
    assert "-m pip install -q pytest==" in planned
    assert ".venv/bin/python -m pytest -q" in planned


def test_with_pytest_the_toy_uses_it_and_needs_no_network(tmp_path: pathlib.Path) -> None:
    planned = _planned(tmp_path, python3_has_pytest=True)
    assert "python3 -m pytest -q" in planned
    assert "venv" not in planned


def test_the_venv_stays_out_of_the_agent_diff() -> None:
    ignored = (TOY / ".gitignore").read_text(encoding="utf-8").split()
    assert ".venv/" in ignored


def test_python_tickets_run_their_tests_through_the_toy_makefile() -> None:
    commands = {
        path.stem: [
            str(raw["run"])
            for raw in yaml.safe_load(path.read_text(encoding="utf-8")).get("assertions") or []
            if raw.get("kind") == "command"
        ]
        for path in sorted(TICKETS.glob("*.yaml"))
        if yaml.safe_load(path.read_text(encoding="utf-8")).get("repo") == "python-toy"
    }
    assert commands, "the python-toy tickets are found"
    assert {stem: runs for stem, runs in commands.items() if runs != ["make test"]} == {}
