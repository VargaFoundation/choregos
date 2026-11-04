"""The orchestrator's activities are English (S45-23, ADR 0098), and nothing a recorded history
names has moved: the registered activity names, the raised error types and the written payload keys
are the recorded ones, and every former module path still resolves."""

from __future__ import annotations

import importlib
import importlib.util
import pathlib
import sys
from types import ModuleType

ROOT = pathlib.Path(__file__).resolve().parents[3]
ACTIVITIES = ROOT / "apps" / "orchestrator" / "src" / "choregos_orchestrator" / "activities"

#: The name every activity of `ALL_ACTIVITIES` is registered under, as recorded before S45-23. A
#: history and every workflow in flight schedule an activity by this name: a rename of a function
#: that moved its `name=` would stop them. The seven `actions` aliases and `confirmer_auto_sync`
#: stay registered under their French names (S34-02).
RECORDED_ACTIVITY_NAMES = {
    "accept_pending_facts",
    "action_state",
    "apply_terraform",
    "await_run",
    "build_hosted_image",
    "cancel_run",
    "charger_l_action",  # lang: fr-ok
    "check_scope_violations",
    "check_window",
    "children_state",
    "claim_hosted_build",
    "close_action",
    "close_human_request",
    "close_out",
    "cloturer_l_action",  # lang: fr-ok
    "collect_run_artifacts",
    "collect_spend",
    "compensate_effect",
    "compenser_l_effet",  # lang: fr-ok
    "confirm_auto_sync",
    "confirmer_auto_sync",  # lang: fr-ok
    "create_human_request",
    "create_release",
    "decide",
    "distill_part",
    "enqueue_merge",
    "ensure_branch",
    "etat_de_l_action",  # lang: fr-ok
    "evaluate_gates",
    "execute_effect",
    "executer_l_effet",  # lang: fr-ok
    "fail_distillation",
    "finding_quality_ratio",
    "finish_provisioning",
    "finish_release",
    "fire_schedules",
    "ingest_alert",
    "ingest_sources",
    "judge_eval_run",
    "list_fixtures",
    "load_action",
    "load_template_steps",
    "load_train_config",
    "mark_awaiting_evidence",
    "mark_release",
    "marquer_l_attente",  # lang: fr-ok
    "memory_ab_report",
    "mirror_state",
    "note_transition_taken",
    "notify",
    "open_pull_request",
    "plan_distillation",
    "prepare_stage",
    "promote",
    "promote_canary_step",
    "propose_transition_action",
    "proposer_l_action_de_transition",  # lang: fr-ok
    "publish_distillation",
    "publish_matrix",
    "reconcile_tracker",
    "record_hosted_build",
    "record_run_outcome",
    "reduce_distillation",
    "release_suspended_run",
    "request_approval",
    "resume_run",
    "rollback",
    "run_eval_cell",
    "run_provision_step",
    "run_smoke",
    "seed_eval_ticket",
    "soak",
    "spawn_children",
    "start_run",
    "start_session_pod",
    "stop_session_pod",
    "summarize_eval_cell",
    "suspend_run",
    "sync_knowledge_source",
    "train_stats",
    "triage_finding",
    "update_status_comment",
    "verify_prod",
    "write_run_lesson",
}

#: The error types the activities raise that a history records (ADR 0098 section 4): they stay
#: French until S45-39, each on a line that says so.
RECORDED_ERROR_TYPES = {
    "AgentIndisponible",
    "InstructionsInvalides",
    "SkillAbsente",
    "EffetRefuse",
    "ActionIntrouvable",
    "ActionPasEncoreVisible",
}


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_23", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def test_activities_have_no_french() -> None:
    """The scan of `tools/check_english.py` (prose, declared names and paths) over every module of
    `activities/` finds nothing: a French name that must stay carries `lang: fr-ok` and its reason,
    and a former module path is a shim with `lang: fr-ok-file`."""
    checker = _checker()
    findings = [
        str(finding)
        for path in sorted(ACTIVITIES.glob("*.py"))
        for finding in checker.file_findings(path.relative_to(ROOT).as_posix(), path.read_text("utf-8"))
    ]
    assert findings == []


def test_registered_activity_names_unchanged() -> None:
    from choregos_orchestrator import activities

    names = [
        definition.name
        for activity in activities.ALL_ACTIVITIES
        if (definition := getattr(activity, "__temporal_activity_definition", None)) is not None
    ]
    assert len(names) == len(activities.ALL_ACTIVITIES) == len(set(names))
    assert set(names) == RECORDED_ACTIVITY_NAMES, {
        "gone": sorted(RECORDED_ACTIVITY_NAMES - set(names)),
        "new": sorted(set(names) - RECORDED_ACTIVITY_NAMES),
    }


def test_recorded_error_types_stay_on_marked_lines() -> None:
    """Every recorded French error type the activities raise sits on a line that says why it stays."""
    found: set[str] = set()
    for path in sorted(ACTIVITIES.glob("*.py")):
        for number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            for error_type in RECORDED_ERROR_TYPES:
                if f'"{error_type}"' in line:
                    found.add(error_type)
                    assert "lang: fr-ok" in line, f"{path.name}:{number}: {error_type} is recorded"
    assert found == RECORDED_ERROR_TYPES


def test_the_english_modules_hold_what_the_former_paths_forwarded() -> None:
    outcome = importlib.import_module("choregos_orchestrator.activities.outcome")
    guard = importlib.import_module("choregos_orchestrator.activities.injection_guard")
    for former in ("bilan", "garde"):
        assert importlib.util.find_spec(f"choregos_orchestrator.activities.{former}") is None  # lang: fr-ok
    assert hasattr(outcome, "collect_spend") and hasattr(outcome, "record_run_outcome")
    assert hasattr(guard, "guard_against_injection") and hasattr(guard, "guard_sources")


def test_the_english_helpers_are_defined() -> None:
    from choregos_orchestrator.activities import execution, gates, interpretation, provisioning, stage

    assert callable(execution.runner_environment) and callable(execution.runtime_class)
    assert callable(execution.hosts_to_reach_directly)
    assert stage.runner_environment is execution.runner_environment
    assert stage.QUEUE_MAX_MINUTES == execution.QUEUE_MAX_MINUTES
    assert callable(gates.added_text_of)
    assert callable(interpretation.message_of) and callable(interpretation.activity_of)
    assert callable(provisioning.step_labels)
    assert provisioning.templates_dir is not None
