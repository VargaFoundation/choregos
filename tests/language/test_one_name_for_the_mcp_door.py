"""The MCP door has one name (#338, ADR 0030: "one MCP door").

The console and the documentation said "MCP door", the API and its contract "MCP gate": the same
object under two names, next to a "guarantee" (gate) that is something else. What the user reads
says "door"; this test refuses the return of the other name in what the API serves, its contract
and the console.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLACES = [
    "apps/api/src",
    "apps/web/src",
    "packages/contracts/openapi.yaml",
    "packages/contracts/ts",
    "packages/core/src/choregos_core/agent_catalogue",
    "templates",
]
SUFFIXES = {".py", ".ts", ".tsx", ".yaml", ".yml", ".md", ".json"}
OTHER_NAME = re.compile(r"\bMCP gate\b", re.IGNORECASE)


def _files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for place in PLACES:
        path = ROOT / place
        if path.is_file():
            files.append(path)
        else:
            files += [f for f in path.rglob("*") if f.suffix in SUFFIXES and "node_modules" not in f.parts]
    return files


def test_the_mcp_door_is_not_called_gate() -> None:
    found = [
        f"{f.relative_to(ROOT)}:{n}"
        for f in _files()
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
        if OTHER_NAME.search(line)
    ]
    assert found == [], found


def test_the_places_exist() -> None:
    assert all((ROOT / place).exists() for place in PLACES)
    assert len(_files()) > 100
