"""The orchestrator's PostgreSQL mode runs what production runs (S47-27).

A schema built by the Alembic migrations and a non-superuser role subject to RLS. A superuser
ignores RLS, so a suite run as one proves nothing about the policies.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from sqlalchemy import text

from .conftest import ON_POSTGRES, Fixture

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


@pytest.mark.skipif(not ON_POSTGRES, reason="CHOREGOS_TEST_ORCH_ON_POSTGRES is not set")
async def test_the_suite_connects_as_a_role_that_is_not_superuser(setup: Fixture) -> None:
    from alembic.script import ScriptDirectory
    from choregos_api.db import session as db_session
    from choregos_api.migrer import configuration

    async with db_session.session_scope() as session:
        rolsuper = (
            await session.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))
        ).scalar()
        versions = {row[0] for row in await session.execute(text("SELECT version_num FROM alembic_version"))}
    assert rolsuper is False
    assert versions == set(ScriptDirectory.from_config(configuration()).get_heads())


def unscoped_session_scope_calls(source: str) -> list[int]:
    """Line numbers of the `session_scope()` calls made without any argument."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or node.args or node.keywords:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        if name == "session_scope":
            lines.append(node.lineno)
    return sorted(lines)


def test_the_detector_finds_an_unscoped_call() -> None:
    source = "async def f():\n    async with session_scope() as s:\n        pass\n    db.session_scope()\n"
    assert unscoped_session_scope_calls(source) == [2, 4]
    assert unscoped_session_scope_calls("session_scope('acme')\nsession_scope(orgs='*')\n") == []


def test_product_code_never_opens_an_unscoped_session() -> None:
    """Justifies the test-side translation of an unscoped call to platform scope."""
    offenders = [
        f"{path.relative_to(SRC)}:{line}"
        for path in sorted(SRC.rglob("*.py"))
        for line in unscoped_session_scope_calls(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"unscoped session_scope() in product code: {offenders}"
