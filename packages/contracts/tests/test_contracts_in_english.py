"""The contracts package is English (S45-06, ADR 0098), and every former helper name still works."""

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


def test_former_helper_names_are_the_english_ones() -> None:
    import choregos_contracts as contracts
    from choregos_contracts import workflow

    assert contracts.empreinte_de_skill is contracts.skill_digest  # lang: fr-ok
    assert workflow.est_un_etat_de_production is workflow.is_production_state  # lang: fr-ok
    assert workflow.PREFIXE_PRODUCTION is workflow.PRODUCTION_PREFIX  # lang: fr-ok
    assert workflow.effet_implicite is workflow.implicit_effect  # lang: fr-ok
    assert workflow.effet_de_la_transition is workflow.transition_effect  # lang: fr-ok
    for name in ("empreinte_de_skill", "skill_digest"):  # lang: fr-ok
        assert name in contracts.__all__
