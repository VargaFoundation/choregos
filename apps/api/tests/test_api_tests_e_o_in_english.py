"""The API tests `test_e*` to `test_o*` are English (S45-20, ADR 0098).

The slice is every tracked `apps/api/tests/test_[e-o]*.py`, plus the files this story renamed out of
that range (`test_announced_edition`, `test_workflow_editing`, `test_work_item_labels`,
`test_template_extensions`, `test_tool_groups`, `test_software_delivery_template`,
`test_run_git_token`, `test_run_tokens`, `test_rbac_matrix`, `test_work_item_migration`).

Left out of the slice: S45-14's `test_mcp_door.py` and
`test_mcp_oauth.py`, S45-04's `test_extension_api_aliases.py`, `test_mcp_write_key.py`, and the
files an earlier S45 story created to check an English rename (`test_mcp_door_in_english.py`, this
file), whose French is the point. A line that must stay French carries `lang: fr-ok`.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
TESTS = "apps/api/tests/"
OWNED = (
    *(f"{TESTS}test_{letter}" for letter in "efghijklmno"),
    f"{TESTS}test_announced_edition.py",
    f"{TESTS}test_workflow_editing.py",
    f"{TESTS}test_work_item_labels.py",
    f"{TESTS}test_template_extensions.py",
    f"{TESTS}test_tool_groups.py",
    f"{TESTS}test_software_delivery_template.py",
    f"{TESTS}test_run_git_token.py",
    f"{TESTS}test_run_tokens.py",
    f"{TESTS}test_rbac_matrix.py",
    f"{TESTS}test_work_item_migration.py",
)
LEFT_OUT = {
    f"{TESTS}test_mcp_door.py",
    f"{TESTS}test_mcp_door_in_english.py",
    f"{TESTS}test_mcp_oauth.py",
    f"{TESTS}test_mcp_write_key.py",
    f"{TESTS}test_extension_api_aliases.py",
    f"{TESTS}test_plugin_api_aliases.py",
    f"{TESTS}test_api_tests_e_o_in_english.py",
}


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_20", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def test_owned_api_tests_have_no_french() -> None:
    checker = _checker()
    try:
        found = checker.scan_tree(OWNED)
    except RuntimeError:  # an exported tree without .git: nothing to scan
        pytest.skip("not a git checkout")
    assert [str(f) for f in found if f.path not in LEFT_OUT] == []
