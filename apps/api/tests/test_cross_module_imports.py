"""The API test modules import each other only through sources that stay put (S45-42).

The alphabetical slices that rename the API test files (S45-19 to S45-21) each rename a few modules.
A test module that imports a helper from a module another slice is about to rename breaks that slice's
import, and the failure shows up in a file the slice never touched. What two modules share lives in
`kits/` instead; the sources listed here are the test modules whose names are English and are not
renamed, so importing from them is safe. Test modules are never shimmed (rule g).
"""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys

TESTS = pathlib.Path(__file__).resolve().parent

#: Test modules, under their final English names, that other test modules may import from.
ALLOWED_SOURCES = frozenset(
    {
        "test_a2a_inbound",
        "test_a2a_push",
        "test_a2a_replicas_and_limits",
        "test_action_relay",
        "test_agent_hosted_sessions",
        "test_agent_realtime",
        "test_agent_release_channels",
        "test_agent_sessions",
        "test_broker",
        "test_broker_result_bounds",
        "test_budget_alerts",
        "test_catalogue_provenance_migration",
        "test_hosted_sources",
        "test_knowledge_distillation_passes",
        "test_knowledge_files",
        "test_knowledge_index",
        "test_knowledge_project_owned",
        "test_knowledge_rights",
        "test_migrations",
        "test_network_policy_api",
        "test_org_slug_aliases",
        "test_outbound_proxy",
        "test_plugin_installer",
        "test_plugin_tracker",
        "test_remote_agents",
        "test_skill_catalogue",
        "test_skills_from_git",
        "test_web_fetch_tool",
        "test_web_search_tool",
        # Not English yet: test_courtier is S46-01's (it renames it test_broker) and test_mcp_porte is
        # renamed by the MCP door story (test_mcp_door). Each of those stories moves its own name out of
        # this list when it renames the module and its importers.
        "test_courtier",
        "test_mcp_porte",
    }
)


def _test_sources(path: pathlib.Path) -> list[tuple[int, str]]:
    """The test modules `path` imports with a relative `from .test_X import` or `from . import test_X`."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom) or node.level != 1:
            continue
        if node.module:
            if node.module.startswith("test_"):
                found.append((node.lineno, node.module))
        else:
            found.extend((node.lineno, a.name) for a in node.names if a.name.startswith("test_"))
    return found


def test_test_modules_import_only_english_sources() -> None:
    offenders = sorted(
        f"{path.name}:{line} imports {source}"
        for path in TESTS.rglob("*.py")
        for line, source in _test_sources(path)
        if source not in ALLOWED_SOURCES
    )
    assert not offenders, "move the helper into apps/api/tests/kits/ and import it from there:\n" + "\n".join(
        offenders
    )


def test_postgres_switch_reads_the_english_variable() -> None:
    env = {k: v for k, v in os.environ.items() if "SUITE" not in k}
    env["CHOREGOS_TEST_SUITE_ON_POSTGRES"] = "1"
    # Whatever the directory pytest runs from, the package this module is imported as must resolve.
    package_root = TESTS
    for _ in (__package__ or "").split("."):
        package_root = package_root.parent
    env["PYTHONPATH"] = os.pathsep.join([str(package_root), *sys.path])
    code = f"import importlib; print(importlib.import_module('{__package__}.conftest').SUITE_ON_POSTGRES)"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False)
    assert out.stdout.strip() == "True", out.stderr
