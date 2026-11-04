"""The orchestrator's tests `i` to `z`, the replay helpers and their histories are English (S45-25).

Nothing a recorded history names has moved except the nine French file stems renamed below: a
history's content is never edited, and the four stems whose French is a recorded workflow id stay
(`FROZEN_PATHS` in `tools/check_english.py`).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

ROOT = pathlib.Path(__file__).resolve().parents[3]
TESTS = ROOT / "apps" / "orchestrator" / "tests"
REPLAY = ROOT / "tests" / "replay"
HISTORIES = REPLAY / "histories"

#: The French-named modules of the set, and the English name each one has now.
RENAMED_MODULES = {
    "test_perimetre_du_run": "test_run_perimeter",
    "test_preuve_attendue": "test_awaited_evidence",
    "test_rbac_du_projet": "test_project_rbac",
    "test_recus_du_prompt": "test_prompt_receipts",
    "test_repartition_des_files": "test_queue_routing",
    "test_reprise_apres_escalade": "test_resume_after_escalation",
    "test_sorties_d_etape": "test_step_outputs",
    "test_tache_humaine": "test_human_task",
    "test_templates_livres": "test_shipped_templates",
    "test_train_au_repos": "test_train_at_rest",
    "test_train_cd_de_demo": "test_train_demo_cd",
    "test_train_ecoute_le_cd": "test_train_listens_to_cd",
    "test_train_et_ticket": "test_train_and_work_item",
    "test_train_se_relaie_tot": "test_train_relays_early",
    "test_worker_greffons": "test_worker_extensions",
}
#: The French-named histories, the English name each one has now, and the test that writes it.
RENAMED_HISTORIES = {
    "action-preuve-S20-08": ("action-evidence-S20-08", "test_awaited_evidence"),
    "train-approbation-S21-21": ("train-approval-S21-21", "test_train_and_work_item"),
    "train-au-repos-S22-24": ("train-at-rest-S22-24", "test_train_at_rest"),
    "train-ecoute-le-cd-S22-18": ("train-listens-to-cd-S22-18", "test_train_listens_to_cd"),
    "train-se-relaie-tot-S22-27": ("train-relays-early-S22-27", "test_train_relays_early"),
    "wi-tache-badge-S20-06": ("wi-human-task-badge-S20-06", "test_human_task"),
    "train-express-ancien-code": ("train-express-old-code", None),
    "wi-migration-ancien-code": ("wi-migration-old-code", None),
    "wi-train-ancien-code": ("wi-train-old-code", None),
}
#: The four histories whose French stem is a recorded workflow id: they keep their name.
FROZEN_STEMS = ("wi-panier-DEMO-1e", "wi-panier-DEMO-2d", "wi-staffing-RH-1d", "wi-staffing-RH-1f")


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_25", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def _owned_files() -> list[pathlib.Path]:
    """The tests `i` to `z`, the two renamed ones that now start with `a` and `h`, the replay modules
    and the histories."""
    tests = sorted(TESTS.glob("test_[i-z]*.py"))
    renamed = [TESTS / "test_awaited_evidence.py", TESTS / "test_human_task.py"]
    replay = sorted(REPLAY.glob("*.py"))
    histories = sorted(HISTORIES.glob("*.json"))
    return tests + renamed + replay + histories


def test_owned_tests_have_no_french() -> None:
    """The scan of `tools/check_english.py` (prose, declared names and paths) over the tests `i` to
    `z`, `tests/replay` and its histories finds nothing; the four frozen stems are skipped by the tool."""
    checker = _checker()
    files = _owned_files()
    assert len(files) > 100, "the scan must see the tests, the replay modules and the histories"
    findings = [
        str(finding)
        for path in files
        for finding in checker.file_findings(path.relative_to(ROOT).as_posix(), path.read_text("utf-8"))
    ]
    assert findings == []


def test_orchestrator_tests_baseline_is_empty() -> None:
    """The `orchestrator-tests` area is at zero: S45-25 brings the last files of it to English.

    Read from the tree, not from `tools/english_baseline/orchestrator-tests.tsv`: the integrator
    regenerates the baselines after each wave (ADR 0098 §9), and a stale baseline entry must not make
    this test depend on the order the stories merged in."""
    checker = _checker()
    findings = [str(finding) for finding in checker.scan_tree(("apps/orchestrator/tests/",))]
    assert findings == []


def test_the_french_modules_are_gone_and_their_english_names_exist() -> None:
    for former, current in RENAMED_MODULES.items():
        assert not (TESTS / f"{former}.py").exists(), former
        assert (TESTS / f"{current}.py").is_file(), current


def test_the_renamed_histories_are_the_renamed_files_and_their_writers_follow() -> None:
    """History files are only renamed, never edited (S45-25): the writer in the test follows the file."""
    for former, (current, writer) in RENAMED_HISTORIES.items():
        assert not (HISTORIES / f"{former}.json").exists(), former
        assert (HISTORIES / f"{current}.json").is_file(), current
        if writer is not None:
            source = (TESTS / f"{writer}.py").read_text("utf-8")
            assert current in source, f"{writer} does not write {current}"
            assert former not in source, f"{writer} still writes {former}"
    for stem in FROZEN_STEMS:
        assert (HISTORIES / f"{stem}.json").is_file(), f"the frozen history {stem} was renamed"
