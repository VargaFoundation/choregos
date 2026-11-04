"""The API tests `test_a*` to `test_d*` are English (S45-19, ADR 0098).

Left out of the slice: S46-01's `test_courtier.py`, and the files an earlier S45 story created
(`*_in_english.py`, `test_configuration_aliases.py`, `test_cross_module_imports.py`), whose French
is the point. A line that must stay French carries `lang: fr-ok`.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
TESTS = "apps/api/tests/"
OWNED = tuple(f"{TESTS}test_{letter}" for letter in "abcd")
LEFT_OUT = {
    f"{TESTS}test_courtier.py",
    f"{TESTS}test_configuration_aliases.py",
    f"{TESTS}test_cross_module_imports.py",
}


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_19", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def _is_owned(path: str) -> bool:
    return path not in LEFT_OUT and not path.endswith("_in_english.py")


def test_owned_api_tests_have_no_french() -> None:
    checker = _checker()
    try:
        found = checker.scan_tree(OWNED)
    except RuntimeError:  # an exported tree without .git: nothing to scan
        pytest.skip("not a git checkout")
    assert [str(f) for f in found if _is_owned(f.path)] == []
