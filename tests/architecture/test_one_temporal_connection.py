"""One way to connect to Temporal (S80-06, ADR 0183).

Every client and worker must share TLS and the payload codec: a process that calls
`Client.connect` itself talks in clear and writes payloads the others cannot read (or cannot read
theirs). Only `choregos_core.temporal_connection` calls it; everything else calls `connect_temporal`.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THE_ONE = ROOT / "packages" / "core" / "src" / "choregos_core" / "temporal_connection.py"


def _sources() -> list[Path]:
    return [
        *ROOT.glob("packages/*/src/**/*.py"),
        *ROOT.glob("apps/*/src/**/*.py"),
        *ROOT.glob("tools/**/*.py"),
    ]


def direct_connections(path: Path) -> list[int]:
    """The lines of `path` that call `<something>.connect(...)` on Temporal's `Client`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names_client = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "temporalio.client"
        and any(alias.name == "Client" for alias in node.names)
        for node in ast.walk(tree)
    )
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "Client"
            and names_client
        ):
            lines.append(node.lineno)
    return lines


def test_only_the_shared_factory_calls_client_connect() -> None:
    offenders = {
        str(path.relative_to(ROOT)): lines
        for path in _sources()
        if path != THE_ONE and (lines := direct_connections(path))
    }
    assert not offenders, f"Temporal clients built outside connect_temporal (ADR 0183): {offenders}"
    assert direct_connections(THE_ONE), "the factory itself no longer calls Client.connect"


def test_the_guard_sees_a_direct_connection(tmp_path: Path) -> None:
    source = tmp_path / "rogue.py"
    source.write_text(
        "async def f():\n    from temporalio.client import Client\n    return await Client.connect('x')\n"
    )
    assert direct_connections(source) == [3]
