"""The contracts package is English (S45-06, ADR 0098)."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_06", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def test_contracts_have_no_french() -> None:
    checker = _checker()
    try:
        found = checker.scan_tree(("packages/contracts/",))
    except RuntimeError:  # an exported tree without .git: nothing to scan
        pytest.skip("not a git checkout")
    assert [str(f) for f in found] == []
