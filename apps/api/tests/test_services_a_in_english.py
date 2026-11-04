"""The action, agent, definition, template and installation services are English (S45-17, ADR 0098),
and every former name keeps resolving to the same object."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import pathlib
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC = "apps/api/src/choregos_api/services/"

OWNED = (
    "actions",
    "agents",
    "agent_lifecycle",
    "definitions",
    "templates",
    "installation",
    "agent_catalogue",
    "connectors",
    "costs",
    "events",
    "projects",
    "memory",
)

#: Former module -> its English module: the old path is gone.
RENAMED_MODULES = {
    "couts": "costs",
    "evenements": "events",
    "memoire": "memory",
    "projets": "projects",
    "gabarits": "templates",
    "catalogue_d_agents": "agent_catalogue",
    "connecteurs": "connectors",
}


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_17", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def test_owned_services_have_no_french() -> None:
    checker = _checker()
    try:
        found = checker.scan_tree([f"{SRC}{name}.py" for name in OWNED])
    except RuntimeError:  # an exported tree without .git: nothing to scan
        pytest.skip("not a git checkout")
    assert [str(f) for f in found] == []


def test_the_former_french_service_modules_are_gone() -> None:
    for former in RENAMED_MODULES:
        assert importlib.util.find_spec(f"choregos_api.services.{former}") is None, former


def test_the_english_keywords_are_the_only_ones() -> None:
    from choregos_api.services import actions, definitions, installation

    assert {"origin", "proposed_by"} <= set(inspect.signature(actions.propose).parameters)
    assert "attempt" in inspect.signature(actions.propose_for_transition).parameters
    assert {"expected_name", "becomes_default", "create_only"} <= set(
        inspect.signature(definitions.publish_workflow).parameters
    )
    assert {"what", "catalogue_provenance"} <= set(
        inspect.signature(installation.install_skills_and_agents).parameters
    )


def test_the_catalogue_keeps_its_diverged_marker() -> None:
    from choregos_api.services import agent_catalogue

    assert agent_catalogue.DIVERGE == "!diverged"


def test_agent_checksum_unchanged() -> None:
    """`digest` is what `agent_versions.checksum` stores (and migration 20261016_1500 recomputes):
    a fixed spec keeps the digest it had before the rename."""
    from choregos_api.schemas import AgentSpec
    from choregos_api.services.agents import digest

    spec = AgentSpec.model_validate({"instructions": "Write the code.", "model": "claude-sonnet-4-5"})
    assert digest(spec) == "sha256:80e9e24de9263e83d24e19fc8c89e633cd5683596db041c7c215f26967d2a774"
