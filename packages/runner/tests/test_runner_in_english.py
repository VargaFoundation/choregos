"""The runner is English (S45-11, ADR 0098), and the former local-tools module still imports."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_11", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def test_runner_has_no_french() -> None:
    checker = _checker()
    try:
        found = checker.scan_tree(("packages/runner/",))
    except RuntimeError:  # an exported tree without .git: nothing to scan
        pytest.skip("not a git checkout")
    assert [str(f) for f in found] == []


def test_former_local_tools_module_still_imports() -> None:
    from choregos_runner import local_tools, outils_locaux

    assert outils_locaux.demarrer_si_absent is local_tools.start_if_absent  # lang: fr-ok alias:internal
    assert outils_locaux.arreter is local_tools.stop  # lang: fr-ok alias:internal
