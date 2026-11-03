"""The orchestrator's tests `a` to `h`, the eval fixtures and the toy repository are English (S45-24).

Nothing a recorded history names has moved: the history written by the dated-action test is the
renamed file, and the former fixture names still resolve (`test_evals.py`).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

ROOT = pathlib.Path(__file__).resolve().parents[3]
TESTS = ROOT / "apps" / "orchestrator" / "tests"
FIXTURES = ROOT / "tests" / "fixtures"
HISTORIES = ROOT / "tests" / "replay" / "histories"

#: The French-named modules of the set, and the English name each one has now.
RENAMED_MODULES = {
    "test_action_a_date": "test_dated_action",
    "test_agent_du_registre": "test_registry_agent",
    "test_besoins_du_gabarit": "test_template_requirements",
    "test_board_en_anglais": "test_board_in_english",
    "test_effets_ecrits": "test_written_effects",
    "test_epingle": "test_pinned_definition",
    "test_file_d_attente": "test_queue_wait",
    "test_gabarit_livraison_logicielle": "test_software_delivery_template",
    "test_gabarits_du_coeur": "test_core_templates",
}
FORMER_TICKETS = (
    "node-slug-longueur",
    "py-bug-arrondi",
    "py-hors-perimetre",
    "py-refactor-sans-regression",
    "py-remise-volume",
    "py-test-manquant",
)


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_24", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def _owned_files() -> list[pathlib.Path]:
    tests = sorted(TESTS.glob("test_[a-h]*.py"))
    tickets = sorted((FIXTURES / "tickets").glob("*.yaml"))
    toy = sorted(
        path
        for path in (FIXTURES / "repos" / "python-toy").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and ".pytest_cache" not in path.parts
    )
    return tests + tickets + toy


def test_owned_tests_and_fixtures_have_no_french() -> None:
    """The scan of `tools/check_english.py` (prose, declared names and paths) over the tests `a` to
    `h`, the reference tickets and the python toy finds nothing. `py-rounding-bug-fr.yaml` is French
    on purpose and says so in its header (`lang: fr-ok-file`): the scan skips it."""
    checker = _checker()
    files = _owned_files()
    assert len(files) > 60, "the scan must see the tests, the tickets and the toy"
    findings = [
        str(finding)
        for path in files
        for finding in checker.file_findings(path.relative_to(ROOT).as_posix(), path.read_text("utf-8"))
    ]
    assert findings == []


def test_the_french_modules_and_tickets_are_gone_and_their_english_names_exist() -> None:
    for former, current in RENAMED_MODULES.items():
        assert not (TESTS / f"{former}.py").exists(), former
        assert (TESTS / f"{current}.py").is_file(), current
    for former in FORMER_TICKETS:
        assert not (FIXTURES / "tickets" / f"{former}.yaml").exists(), former
    assert not (FIXTURES / "repos" / "python-toy" / "src" / "panier").exists()
    assert (FIXTURES / "repos" / "python-toy" / "src" / "cart" / "cart.py").is_file()


def test_the_dated_action_history_is_the_renamed_file_and_is_written_under_that_name() -> None:
    """History files are never edited, only renamed (S45-24): the writer in the test follows the file."""
    assert (HISTORIES / "wi-dated-action-S20-05.json").is_file()
    assert not (HISTORIES / "wi-action-a-date-S20-05.json").exists()  # lang: fr-ok
    assert '"wi-dated-action-S20-05.json"' in (TESTS / "test_dated_action.py").read_text("utf-8")

