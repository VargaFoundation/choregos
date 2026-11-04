"""The rest of the core is English (S45-08, ADR 0098)."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]

SOURCES = tuple(
    f"packages/core/src/choregos_core/{name}"
    for name in (
        "__init__.py",
        "access.py",
        "admission.py",
        "secrets.py",
        "catalogue.py",
        "catalogues.py",
        "cron.py",
        "egress.py",
        "errors.py",
        "exit_codes.py",
        "hosted.py",
        "images.py",
        "llm_guardrail.py",
        "llm_spans.py",
        "mcp.py",
        "mcp_catalogue/",
        "memory_bindings.py",
        "memory_scope.py",
        "models.py",
        "network.py",
        "outbound_proxy.py",
        "prompt_loop.py",
        "quotas.py",
        "redaction.py",
        "telemetry.py",
        "web_search.py",
    )
)
TESTS = (
    "test_access",
    "test_backend_enabled",
    "test_catalogue",
    "test_cron",
    "test_docs_updated",
    "test_egress",
    "test_egress_network_bounds",
    "test_injection",
    "test_llm_guardrail",
    "test_llm_spans",
    "test_mcp_catalogue",
    "test_memory_bindings",
    "test_memory_scope",
    "test_models_resolution",
    "test_native_sidecars",
    "test_network",
    "test_numbers_measured",
    "test_procedures_format",
    "test_prompt_loop",
    "test_redaction",
    "test_secrets",
    "test_skill_catalogue",
    "test_telemetry",
    "test_web_search_bounds",
    "test_core_in_english",
)


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_08", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def test_core_outside_the_dsl_has_no_french() -> None:
    checker = _checker()
    prefixes = (*SOURCES, *(f"packages/core/tests/{name}.py" for name in TESTS))
    try:
        found = checker.scan_tree(prefixes)
    except RuntimeError:  # an exported tree without .git: nothing to scan
        pytest.skip("not a git checkout")
    assert [str(f) for f in found] == []


def test_the_demo_catalogue_still_loads() -> None:
    """An operator's catalogue, written with the former key, keeps loading."""
    from choregos_core.catalogue import load_catalogue

    path = ROOT / "demo" / "outils" / "catalogue.yaml"  # lang: fr-ok
    if not path.exists():
        pytest.skip("no demo catalogue in this tree")
    assert load_catalogue(path.read_text(encoding="utf-8")).tools
