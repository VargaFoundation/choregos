# SPDX-License-Identifier: Apache-2.0
"""Does `python -m choregos_api.migrate` play the core from anywhere, and an extension's branch?

The extension is installed for real (a `.dist-info` on disk that declares the group
`choregos.migrations`); it is `migrate` that finds it, as in the chart's Job.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, inspect, text

API_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def _core_head() -> str:
    from alembic.script import ScriptDirectory
    from choregos_api.migrate import configuration

    (head,) = ScriptDirectory.from_config(configuration(with_extensions=False)).get_heads()
    return head


@pytest.fixture
def database(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    file = tmp_path / "migrated.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{file}")
    from choregos_api.config import reset_settings_cache

    reset_settings_cache()
    yield f"sqlite:///{file}"
    reset_settings_cache()


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _versions(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return {r[0] for r in connection.execute(text("SELECT version_num FROM alembic_version"))}
    finally:
        engine.dispose()


def test_the_core_migrates_from_any_directory(database: str, tmp_path: pathlib.Path) -> None:
    """The former command required `/app/apps/api` as the working directory."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    done = subprocess.run(
        [sys.executable, "-m", "choregos_api.migrate"],
        cwd=elsewhere,
        env={**os.environ, "PYTHONPATH": str(API_SRC)},
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert done.returncode == 0, done.stderr[-1500:]
    assert {"organizations", "projects", "audit_log", "gateway_keys"} <= _tables(database)
    assert _versions(database) == {_core_head()}


EXTENSION_REVISION = '''
"""The extension's notes: ITS table, attached to the core by a foreign key."""

import sqlalchemy as sa
from alembic import op

revision = "extension0001"
down_revision = None
branch_labels = ("extension_schema",)
depends_on = "{head}"


def upgrade() -> None:
    op.create_table(
        "extension_notes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("extension_notes")
'''


def _install(root: pathlib.Path, name: str, source: str, group: str, target: str) -> None:
    (root / f"{name}.py").write_text(source, encoding="utf-8")
    info = root / f"{name}-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 0.1.0\n", encoding="utf-8")
    (info / "entry_points.txt").write_text(f"[{group}]\n{name} = {target}\n", encoding="utf-8")


@pytest.fixture
def extension_with_schema(tmp_path: pathlib.Path) -> Iterator[pathlib.Path]:
    root = tmp_path / "site"
    versions = root / "extension_schema_versions"
    versions.mkdir(parents=True)
    (versions / "extension0001_notes.py").write_text(
        EXTENSION_REVISION.replace("{head}", _core_head()), encoding="utf-8"
    )
    module = f"import pathlib\nLOCATION = pathlib.Path(__file__).parent / {versions.name!r}\n"
    _install(root, "extension_schema", module, "choregos.migrations", "extension_schema:LOCATION")
    sys.path.insert(0, str(root))
    try:
        yield root
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("extension_schema", None)


def test_an_extensions_branch_is_played_after_the_core(
    database: str, extension_with_schema: pathlib.Path
) -> None:
    from choregos_api.migrate import main

    main([])

    assert "extension_notes" in _tables(database)
    assert "organizations" in _tables(database)
    # A single row, and that is normal: the branch `depends_on` the core's head, and Alembic only
    # keeps the most dependent revision, which covers the other. The next test shows that the
    # core's head comes back alone when the branch goes back down.
    assert _versions(database) == {"extension0001"}


def test_the_extensions_branch_goes_back_down_alone(
    database: str, extension_with_schema: pathlib.Path
) -> None:
    """Uninstalling an extension must be able to remove ITS schema without touching the core's."""
    from choregos_api.migrate import main

    main([])
    main(["downgrade", "extension_schema@base"])

    assert "extension_notes" not in _tables(database)
    assert _versions(database) == {_core_head()}


def test_a_declared_but_missing_location_stops_the_command(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "site"
    root.mkdir()
    module = "LOCATION = '/nowhere/versions'\n"
    _install(root, "extension_empty", module, "choregos.migrations", "extension_empty:LOCATION")
    sys.path.insert(0, str(root))
    try:
        from choregos_api.migrate import extension_locations

        with pytest.raises(RuntimeError, match=r"extension_empty.*does not exist"):
            extension_locations()
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("extension_empty", None)
