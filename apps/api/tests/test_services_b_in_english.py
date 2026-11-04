"""The ticket, journey, receipt, routing and skill services, and the English-named rest, are English
(S45-18, ADR 0098), and every former name keeps resolving to the same object."""

from __future__ import annotations

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


def test_the_services_package_exports_the_english_names() -> None:
    from choregos_api import services
    from choregos_api.services import routing, tickets

    assert services.WorkItemBirth is routing.WorkItemBirth
    assert services.new_work_item is routing.new_work_item
    assert services.record_labels is routing.record_labels
    assert services.internal_work_item_key is tickets.internal_work_item_key
    assert services.tracker_is_internal is tickets.tracker_is_internal
    assert services.store_outputs is tickets.store_outputs
    assert services.create_work_item is tickets.create_work_item
    assert services.timeline is tickets.timeline
    for name in ("WorkItemBirth", "new_work_item", "journey", "active_workflow_named", "store_outputs"):
        assert name in services.__all__


def test_the_former_french_module_paths_are_gone() -> None:
    for former in ("routage", "recus", "parcours"):
        assert importlib.util.find_spec(f"choregos_api.services.{former}") is None


def test_the_former_keywords_are_gone() -> None:
    from choregos_api.services import receipts, tickets

    receipt_keywords = set(inspect.signature(receipts.record).parameters)
    assert {"outputs", "genre"} <= receipt_keywords and "sorties" not in receipt_keywords
    creation_keywords = set(inspect.signature(tickets.create_work_item).parameters)
    assert {"key", "canal"} <= creation_keywords and "cle" not in creation_keywords


def test_patching_project_tracker_reaches_the_tracker_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.services import tickets

    marker = object()
    monkeypatch.setattr(tickets, "project_tracker", marker)
    assert tickets.project_tracker is marker
    monkeypatch.undo()
    assert tickets.project_tracker is not marker


def test_no_service_module_counts_french() -> None:
    checker = _checker()
    try:
        assert not [p for p in SERVICES.glob("*.py") if _counts(checker, p)]
    except RuntimeError:
        pytest.skip("not a git checkout")


def _counts(checker: ModuleType, path: pathlib.Path) -> list[object]:
    return list(checker.scan_tree([str(path.relative_to(ROOT))]))
