"""The workflow DSL, gates, policy, instructions and presets are English (S45-07, ADR 0098)."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]

SOURCES = (
    "packages/core/src/choregos_core/dsl/",
    "packages/core/src/choregos_core/gates/",
    "packages/core/src/choregos_core/instructions.py",
    "packages/core/src/choregos_core/policy.py",
    "packages/core/src/choregos_core/policy_ceiling.py",
    "packages/core/src/choregos_core/domain.py",
    "packages/core/src/choregos_core/presets/",
)
TESTS = (
    "test_transition_action",
    "test_already_fixed",
    "test_artifact_flow",
    "test_attempts_ceiling",
    "test_demo_map",
    "test_declared_outputs",
    "test_dsl_validation",
    "test_edition",
    "test_engine",
    "test_requirements",
    "test_gate_outside_the_tree",
    "test_gates",
    "test_github_software_delivery_template",
    "test_governed_delivery_template",
    "test_graph",
    "test_graph_labels",
    "test_job_actor",
    "test_legacy_declarations",
    "test_output_schema",
    "test_policy",
    "test_policy_ceiling",
    "test_policy_ceiling_network",
    "test_process",
    "test_process_view_names_the_agent",
    "test_spawn",
    "test_human_task",
    "test_deployment_templates",
    "test_trains",
    "test_workflow_budget",
    "test_dsl_in_english",
)


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_07", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def test_dsl_gates_policy_have_no_french() -> None:
    checker = _checker()
    prefixes = (*SOURCES, *(f"packages/core/tests/{name}.py" for name in TESTS))
    try:
        found = checker.scan_tree(prefixes)
    except RuntimeError:  # an exported tree without .git: nothing to scan
        pytest.skip("not a git checkout")
    assert [str(f) for f in found] == []
