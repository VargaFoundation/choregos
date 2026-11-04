# SPDX-License-Identifier: Apache-2.0
"""The former entry points of the API keep working until 0.23.0 (S45-05, ADR 0098 §3 (f))."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

API_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def test_python_m_migrer_still_runs(tmp_path: pathlib.Path) -> None:  # lang: fr-ok alias:external
    """`python -m choregos_api.migrer heads` exits 0 on a temporary SQLite database, as the Job of a
    deployment that did not switch to `choregos_api.migrate` yet."""
    database = tmp_path / "former.db"
    done = subprocess.run(
        [sys.executable, "-m", "choregos_api.migrer", "heads"],  # lang: fr-ok alias:external
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(API_SRC),
            "CHOREGOS_DATABASE_URL": f"sqlite+aiosqlite:///{database}",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert done.returncode == 0, done.stderr[-1500:]
    assert "(head)" in done.stdout


def test_migrate_configuration_accepts_the_former_keyword() -> None:
    from choregos_api.migrate import configuration

    for value in (True, False):
        former = configuration(avec_les_greffons=value)  # lang: fr-ok alias:external
        current = configuration(with_extensions=value)
        assert former.get_main_option("version_locations") == current.get_main_option("version_locations")
    winning = configuration(with_extensions=True, avec_les_greffons=False)  # lang: fr-ok alias:external
    assert winning.get_main_option("version_locations") == (
        configuration(with_extensions=False).get_main_option("version_locations")
    ), "the former keyword wins when given"


def test_former_migration_names_are_the_new_ones() -> None:
    from choregos_api import migrate, migrer  # lang: fr-ok alias:external

    assert migrer.emplacements_des_greffons is migrate.extension_locations  # lang: fr-ok alias:external
    assert migrer.GROUPE_DE_MIGRATIONS == migrate.MIGRATIONS_GROUP  # lang: fr-ok alias:external
    assert migrer.configuration is migrate.configuration
    assert migrer.main is migrate.main


def test_outils_shim_exposes_the_cli() -> None:  # lang: fr-ok alias:external
    from choregos_api import admin_cli, outils  # lang: fr-ok alias:external

    assert outils.app is admin_cli.app
    assert outils.main is admin_cli.main


def test_the_console_script_points_at_the_english_module() -> None:
    pyproject = (API_SRC.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert 'choregos-admin = "choregos_api.admin_cli:main"' in pyproject
