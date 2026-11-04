"""The API tests `test_p*` to `test_z*` are English, and so is the whole API test folder (S45-21, ADR 0098).

The slice is every tracked `apps/api/tests/test_[p-z]*.py`, plus the files this story renamed out of
that range (`test_bounded_pool`, `test_agent_registry`, `test_bare_routers`, `test_admin_sections`,
`test_authentication_security`, `test_human_task`, `test_one_work_item_builder`,
`test_multiple_workflows`).

Left out of the slice: S46-01's `test_courtier.py` and `test_marketdata_courtier.py`, S45-14's
`test_scoped_tokens.py`, S45-04's `test_plugin_api_aliases.py`, and the files an earlier S45 story
created to check an English rename (`test_*_in_english.py`, this file), whose French is the point.
A line that must stay French carries `lang: fr-ok`.
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
    f"{TESTS}test_p",
    f"{TESTS}test_q",
    f"{TESTS}test_r",
    f"{TESTS}test_s",
    f"{TESTS}test_t",
    f"{TESTS}test_u",
    f"{TESTS}test_v",
    f"{TESTS}test_w",
    f"{TESTS}test_x",
    f"{TESTS}test_y",
    f"{TESTS}test_z",
    f"{TESTS}test_bounded_pool.py",
    f"{TESTS}test_agent_registry.py",
    f"{TESTS}test_bare_routers.py",
    f"{TESTS}test_admin_sections.py",
    f"{TESTS}test_authentication_security.py",
    f"{TESTS}test_human_task.py",
    f"{TESTS}test_one_work_item_builder.py",
    f"{TESTS}test_multiple_workflows.py",
)
#: S46-01's tests keep their French until that story renames them.
S46_TESTS = {
    f"{TESTS}test_courtier.py",
    f"{TESTS}test_marketdata_courtier.py",
}
LEFT_OUT = {
    *S46_TESTS,
    f"{TESTS}test_scoped_tokens.py",
    f"{TESTS}test_plugin_api_aliases.py",
    f"{TESTS}test_api_tests_p_z_in_english.py",
}


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_21", ROOT / "tools" / "check_english.py")
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
    assert [str(f) for f in found if f.path not in LEFT_OUT and not f.path.endswith("_in_english.py")] == []


def test_api_tests_baseline_holds_only_s44_files() -> None:
    """Read the live scan, not `api-tests.tsv` (which a later story lowers): the only API test files
    still holding French are S46-01's, and the files an earlier story wrote to check a rename."""
    checker = _checker()
    try:
        found = checker.scan_tree((TESTS,))
    except RuntimeError:
        pytest.skip("not a git checkout")
    with_findings = {f.path for f in found if not f.path.endswith("_in_english.py")}
    assert sorted(with_findings - S46_TESTS) == []
