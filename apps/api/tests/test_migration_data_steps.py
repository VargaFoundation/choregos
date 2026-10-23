"""Every migration step that writes rows opens every organisation first (S40-22e).

The chart's Job migrates as the API's own role, under forced RLS (`tests/charts/
test_hook_de_migration.py`): an `UPDATE`, `DELETE` or `INSERT … SELECT` that runs before
`set_config('app.current_orgs', '*', true)` sees zero rows and says nothing. The release channels'
downgrade did exactly that: it deleted no channel pin, and the `NOT NULL` after it failed on them.
`test_migrations_postgres.py` proves two such steps over rows; this reads every migration, upgrade
and downgrade, so a new data step cannot forget the scope without turning the suite red.
"""

from __future__ import annotations

import ast
import pathlib
import re

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "src" / "choregos_api" / "migrations" / "versions"

#: What writes rows: plain SQL, SQLAlchemy Core, and the helpers the migrations name after it.
WRITES = re.compile(
    r"\b(UPDATE\s+\w+\s+SET|DELETE\s+FROM|INSERT\s+INTO)\b|\.update\(\)|\.delete\(\)|bulk_insert|\bBACKFILL\w*",
    re.IGNORECASE,
)
OPENS = "app.current_orgs"


def _steps(path: pathlib.Path) -> dict[str, tuple[bool, bool]]:
    """`upgrade` and `downgrade`: whether each writes rows and whether it opens the scope,
    counting what the module's own functions and string constants they use do."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    constants = {
        target.id: str(node.value.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    def text(name: str, seen: frozenset[str]) -> str:
        node = functions[name]
        own = ast.get_source_segment(source, node) or ""
        used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        parts = [own, *(constants[n] for n in used if n in constants)]
        parts += [text(n, seen | {name}) for n in used if n in functions and n not in seen | {name}]
        return "\n".join(parts)

    return {
        step: (bool(WRITES.search(body := text(step, frozenset()))), OPENS in body)
        for step in ("upgrade", "downgrade")
        if step in functions
    }


def test_the_scan_finds_the_data_steps() -> None:
    """The scan itself: it sees the steps known to write rows, or it proves nothing."""
    writing = {
        (path.name, step)
        for path in VERSIONS.glob("*.py")
        for step, (writes, _) in _steps(path).items()
        if writes
    }
    assert ("20261005_1800_un_ticket_epingle_sa_definition.py", "upgrade") in writing
    assert ("20261017_0900_agent_release_channels.py", "downgrade") in writing
    assert ("20261016_1900_s39_data_under_rls.py", "upgrade") in writing, "a backfill imported from a module"
    assert len(writing) >= 12


def test_every_data_step_opens_every_organisation() -> None:
    blind = sorted(
        f"{path.name}::{step}"
        for path in VERSIONS.glob("*.py")
        for step, (writes, opens) in _steps(path).items()
        if writes and not opens
    )
    assert not blind, (
        "these steps write rows without `set_config('app.current_orgs', '*', true)` first: under the "
        f"migration job's role they see zero rows — {blind}"
    )
