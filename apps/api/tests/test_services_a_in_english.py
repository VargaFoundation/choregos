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

#: Former module -> its English module: each old path is the very same module object.
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


def test_former_service_names_still_import() -> None:
    from choregos_api.services import (
        actions,
        agent_catalogue,
        agents,
        connectors,
        costs,
        definitions,
        events,
        memory,
        templates,
    )

    assert agents.AgentIndisponible is agents.AgentUnavailable  # type: ignore[attr-defined]
    assert templates.livraison is templates.delivery  # type: ignore[attr-defined]
    assert importlib.import_module("choregos_api.services.gabarits") is templates
    assert importlib.import_module("choregos_api.services.evenements").persist_event is events.persist_event
    for former, current in RENAMED_MODULES.items():
        assert importlib.import_module(f"choregos_api.services.{former}") is importlib.import_module(
            f"choregos_api.services.{current}"
        )
    assert agents.depense_du_jour is agents.spend_today  # type: ignore[attr-defined]
    assert agents.depense_du_mois is agents.spend_this_month  # type: ignore[attr-defined]
    assert agents.resoudre_l_agent is agents.resolve_agent  # type: ignore[attr-defined]
    assert agents.erreurs_d_une_version is agents.version_errors  # type: ignore[attr-defined]
    assert agents.empreinte is agents.digest  # type: ignore[attr-defined]
    assert actions.proposer_pour_une_transition is not None
    assert actions.rang is actions.rank  # type: ignore[attr-defined]
    assert actions.signaler_une_preuve is actions.signal_evidence  # type: ignore[attr-defined]
    assert definitions.publier_workflow is not None
    assert definitions.workflow_actif is definitions.active_workflow_named  # type: ignore[attr-defined]
    assert definitions.workflow_du_ticket is definitions.work_item_workflow  # type: ignore[attr-defined]
    assert definitions.derniere_version is definitions.latest_version  # type: ignore[attr-defined]
    assert (
        definitions.signaler_ce_que_la_politique_n_applique_pas  # type: ignore[attr-defined]
        is definitions.report_unenforced_policy
    )
    assert memory.MIN_TICKETS_PAR_GROUPE == memory.MIN_WORK_ITEMS_PER_GROUP  # type: ignore[attr-defined]
    assert costs.record_cost is importlib.import_module("choregos_api.services.couts").record_cost
    assert agent_catalogue.named_agents is agent_catalogue.agents_nommes  # type: ignore[attr-defined]
    assert agent_catalogue.install_named_agents is agent_catalogue.installer_les_agents_nommes  # type: ignore[attr-defined]
    assert agent_catalogue.DIVERGE == "!diverged"
    assert connectors.human_floor is importlib.import_module("choregos_api.services.connecteurs").human_floor


def test_a_patch_of_a_former_name_reaches_the_english_one(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.services import definitions

    marker = object()
    monkeypatch.setattr(definitions, "derniere_version", marker)
    assert definitions.latest_version is marker


def test_former_keywords_still_work_through_the_wrappers() -> None:
    from choregos_api.services import actions, definitions, installation

    assert {"origine", "propose_par"} <= set(inspect.signature(actions.proposer).parameters)
    assert "tentative" in inspect.signature(actions.proposer_pour_une_transition).parameters
    assert {"nom_attendu", "devient_le_defaut", "creation_seule"} <= set(
        inspect.signature(definitions.publier_workflow).parameters
    )
    assert {"quoi", "du_catalogue"} <= set(
        inspect.signature(installation.installer_skills_et_agents).parameters
    )
    assert {"what", "catalogue_provenance"} <= set(
        inspect.signature(installation.install_skills_and_agents).parameters
    )


def test_agent_checksum_unchanged() -> None:
    """`digest` is what `agent_versions.checksum` stores (and migration 20261016_1500 recomputes):
    a fixed spec keeps the digest it had before the rename."""
    from choregos_api.schemas import AgentSpec
    from choregos_api.services.agents import digest

    spec = AgentSpec.model_validate({"instructions": "Write the code.", "model": "claude-sonnet-4-5"})
    assert digest(spec) == "sha256:80e9e24de9263e83d24e19fc8c89e633cd5683596db041c7c215f26967d2a774"
