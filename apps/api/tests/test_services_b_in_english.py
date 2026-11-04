"""The ticket, journey, receipt, routing and skill services, and the English-named rest, are English
(S45-18, ADR 0098), and every former name keeps resolving to the same object."""

from __future__ import annotations

import csv
import importlib
import importlib.util
import inspect
import pathlib
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SERVICES = ROOT / "apps/api/src/choregos_api/services"
SRC = "apps/api/src/choregos_api/services/"

#: What this story owns: the nine services it rewrote and the English-named one it touched.
OWNED = (
    "__init__",
    "tickets",
    "journey",
    "receipts",
    "routing",
    "skill_gate",
    "skill_review",
    "skill_sources",
    "skills",
    "agent_governance",
    "agent_releases",
)

#: Former module -> its English module: each old path is the very same module object.
RENAMED_MODULES = {"routage": "routing", "recus": "receipts", "parcours": "journey"}


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_18", ROOT / "tools" / "check_english.py")
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


def test_orchestrator_imports_still_resolve() -> None:
    from choregos_api import services
    from choregos_api.services import routing, tickets

    assert services.Naissance is routing.WorkItemBirth  # type: ignore[attr-defined]
    assert services.nouveau_ticket is routing.new_work_item  # type: ignore[attr-defined]
    assert services.noter_les_etiquettes is routing.record_labels  # type: ignore[attr-defined]
    assert services.choisir_workflow is routing.choose_workflow  # type: ignore[attr-defined]
    assert services.cle_de_ticket_interne is tickets.internal_work_item_key  # type: ignore[attr-defined]
    assert services.le_tracker_est_interne is tickets.tracker_is_internal  # type: ignore[attr-defined]
    assert services.ranger_les_sorties is tickets.store_outputs  # type: ignore[attr-defined]
    assert services.creer_un_ticket is tickets.create_work_item  # type: ignore[attr-defined]
    assert services.chronologie is tickets.timeline  # type: ignore[attr-defined]
    assert services.DOCUMENTS_LOGICIELS is tickets.SOFTWARE_DOCUMENTS  # type: ignore[attr-defined]
    # The English names are the ones the package exports first.
    assert services.WorkItemBirth is routing.WorkItemBirth
    assert services.new_work_item is routing.new_work_item
    assert services.record_labels is routing.record_labels
    assert services.internal_work_item_key is tickets.internal_work_item_key
    assert services.tracker_is_internal is tickets.tracker_is_internal
    assert services.store_outputs is tickets.store_outputs
    for name in ("Naissance", "nouveau_ticket", "parcours", "workflow_actif", "ranger_les_sorties"):
        assert name in services.__all__


def test_former_modules_are_the_english_ones() -> None:
    from choregos_api import services

    former_function = services.__dict__["parcours"]  # lang: fr-ok alias:internal
    for former, current in RENAMED_MODULES.items():
        assert importlib.import_module(f"choregos_api.services.{former}") is importlib.import_module(
            f"choregos_api.services.{current}"
        )
    services.__dict__["parcours"] = former_function  # lang: fr-ok alias:internal


def test_former_names_of_the_renamed_modules_resolve() -> None:
    routing = importlib.import_module("choregos_api.services.routing")
    receipts = importlib.import_module("choregos_api.services.receipts")
    journey = importlib.import_module("choregos_api.services.journey")
    skills = importlib.import_module("choregos_api.services.skills")

    assert routing.Naissance is routing.WorkItemBirth
    assert routing.nouveau_ticket_d_essai is routing.new_trial_work_item
    assert routing.valider_les_champs is routing.validate_fields
    assert routing.verifier_la_naissance is routing.verify_birth
    assert receipts.LUE == receipts.READ == "read"
    assert receipts.PRODUITE == receipts.PRODUCED == "produced"
    assert receipts.consigner is receipts.record
    assert receipts.empreinte is receipts.digest
    assert receipts.lu_par_le_prompt is receipts.read_by_the_prompt
    assert receipts.passages_du_ticket is receipts.work_item_handoffs
    assert journey.parcours is journey.journey
    assert journey.arete_du_deplacement is journey.move_edge
    # plugin_installer imports these three from `.skills`, and the knowledge distiller two of them.
    assert skills.SkillRefusee is skills.SkillRefused
    assert skills.valider is skills.validate
    assert skills.empreinte is skills.digest
    assert skills.MAX_OCTETS == skills.MAX_BYTES
    assert skills.entete is skills.front_matter


def test_a_former_keyword_still_reaches_the_new_one() -> None:
    from choregos_api.services import receipts, tickets

    assert {"outputs", "sorties", "genre"} <= set(inspect.signature(receipts.record).parameters)
    assert {"key", "cle", "canal"} <= set(inspect.signature(tickets.create_work_item).parameters)


def test_patching_tracker_du_projet_patches_project_tracker(  # lang: fr-ok - the name the story gives
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_api.services import tickets

    marker = object()
    monkeypatch.setattr(tickets, "tracker_du_projet", marker)  # lang: fr-ok alias:internal
    assert tickets.project_tracker is marker
    monkeypatch.undo()
    assert tickets.tracker_du_projet is tickets.project_tracker  # lang: fr-ok alias:internal

    other = object()
    monkeypatch.setattr(tickets, "tracker_is_internal", other)
    assert tickets.le_tracker_est_interne is other  # lang: fr-ok alias:internal


def test_only_the_broker_is_left() -> None:
    checker = _checker()
    lines = [
        row
        for row in csv.reader(
            (ROOT / "tools/english_baseline/api-services.tsv").read_text(encoding="utf-8").splitlines(),
            delimiter="\t",
        )
        if row and not row[0].startswith("#")
    ]
    try:
        still_french = [row[0] for row in lines if checker.scan_tree([row[0]]) and (ROOT / row[0]).exists()]
    except RuntimeError:
        pytest.skip("not a git checkout")
    # A baseline line that still counts French must name the broker's shim, nothing else.
    assert all(path.endswith("services/courtier.py") for path in still_french), still_french
    assert not [p for p in SERVICES.glob("*.py") if p.name != "courtier.py" and _counts(checker, p)]


def _counts(checker: ModuleType, path: pathlib.Path) -> list[object]:
    return list(checker.scan_tree([str(path.relative_to(ROOT))]))
