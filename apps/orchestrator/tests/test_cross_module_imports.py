"""Orchestrator test modules share helpers through `kits/`, never through each other's French names (S45-43).

A test module that imports a helper from another test module ties the two together: renaming one
(S45-24, S45-25) breaks the other. The helpers that were imported from French-named modules live in
`tests/kits/` under English names; the modules that stay as import sources are listed here, and
nothing else may be one.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from .conftest import recording_history

TESTS = pathlib.Path(__file__).resolve().parent

#: The test modules another test module may import from (all English-named).
ALLOWED_SOURCES = {
    "test_agent_monthly_cap",
    "test_backend_toggle_enforced",
    "test_budget_reservation",
    "test_code_agent_step",
    "test_fan_out",
    "test_gateway_key_through_the_secret",
    "test_interpreter",
    "test_lost_run",
    "test_project_day_team",
    "test_remote_agent_step",
    "test_resolver_sees_the_gateway",
    "test_restarted_interpreter_resumes",
    "test_result_from_journal",
    "test_suspend_around_human",
}

#: Names that used to be imported from French-named modules and now live in `kits/`.
MOVED_NAMES = {
    "ADR_PROPOSE",
    "DEPOT",
    "_agents",
    "_ci",
    "_decider",
    "_demandes",
    "_demarrer",
    "_etat",
    "_etude",
    "_jusqu_a",
    "_jusqu_au_train",
    "_politique_de_prod",
    "_projet_dev",
    "_releases",
    "_verdict",
    "_verificateur_de_prod",
    "_worker_avec",
}


def _cross_imports() -> list[tuple[str, str, list[str]]]:
    found: list[tuple[str, str, list[str]]] = []
    for path in sorted(TESTS.glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.ImportFrom)
                and node.level == 1
                and (node.module or "").startswith("test_")
            ):
                found.append((path.name, node.module or "", [alias.name for alias in node.names]))
    return found


def test_test_modules_import_only_english_sources() -> None:
    imports = _cross_imports()
    assert imports, "the walk found no cross-module import: the scan is broken"
    wrong_source = [f"{who} <- {source}" for who, source, _ in imports if source not in ALLOWED_SOURCES]
    assert not wrong_source, f"imports from a module that is not an allowed source: {wrong_source}"
    stale = [
        f"{who} <- {source}: {name}"
        for who, source, names in imports
        for name in names
        if name in MOVED_NAMES
    ]
    assert not stale, f"a helper that moved to `kits/` is imported from a test module: {stale}"
    kit_imports = [
        path.name
        for path in (TESTS / "kits").glob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("test_")
    ]
    assert not kit_imports, f"a kit imports a test module: {kit_imports}"
    gone = [source for source in ALLOWED_SOURCES if not (TESTS / f"{source}.py").exists()]
    assert not gone, f"an allowed source no longer exists, drop it from the list: {gone}"


def test_recording_reads_the_english_variable_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_RECORD_HISTORY", "english")
    monkeypatch.setenv("CHOREGOS_ARCHIVER_HISTORIQUE", "1")  # lang: fr-ok alias:internal
    assert recording_history() == "english"
    monkeypatch.delenv("CHOREGOS_RECORD_HISTORY")
    assert recording_history() == "1"
    monkeypatch.delenv("CHOREGOS_ARCHIVER_HISTORIQUE")  # lang: fr-ok alias:internal
    assert recording_history() is None
