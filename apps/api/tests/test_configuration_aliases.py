# SPDX-License-Identifier: Apache-2.0
"""The former entry points of the API are gone since 0.24.0 (S45-05, S45-49, ADR 0098 §3 (f))."""

from __future__ import annotations

import importlib
import os
import pathlib
import subprocess
import sys

import pytest

API_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def test_python_m_of_the_former_migration_module_fails(tmp_path: pathlib.Path) -> None:
    """The chart's Job runs `python -m choregos_api.migrate`; the former module no longer runs."""
    database = tmp_path / "former.db"
    done = subprocess.run(
        [sys.executable, "-m", "choregos_api.migrer", "heads"],  # lang: fr-ok - removed, asserted gone
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
    assert done.returncode != 0
    assert "No module named" in done.stderr


@pytest.mark.parametrize(
    "former",
    [
        "choregos_api.migrer",  # lang: fr-ok - removed, asserted gone
        "choregos_api.outils",  # lang: fr-ok - removed, asserted gone
    ],
)
def test_the_former_api_modules_are_gone(former: str) -> None:
    with pytest.raises(ImportError):
        importlib.import_module(former)


def test_migrate_configuration_refuses_the_former_keyword() -> None:
    from choregos_api.migrate import configuration

    with pytest.raises(TypeError):
        configuration(**{"avec_les_greffons": False})  # lang: fr-ok - removed, asserted gone
    assert configuration(with_extensions=False).get_main_option("version_locations")
