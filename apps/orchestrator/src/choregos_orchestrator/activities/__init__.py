# SPDX-License-Identifier: Apache-2.0
"""Activités de l'orchestrateur : tout ce qui touche au monde extérieur.

Elles sont idempotentes, typées, et n'appellent jamais un modèle directement.
"""

from __future__ import annotations

from . import (
    actions,
    bilan,
    evals,
    execution,
    findings,
    gates,
    knowledge,
    memory,
    provisioning,
    schedules,
    scm,
    stage,
    suspension,
    tracker,
    train,
)

ALL_ACTIVITIES = [
    knowledge.sync_knowledge_source,
    actions.load_action,
    actions.execute_effect,
    actions.compensate_effect,
    actions.close_action,
    actions.propose_transition_action,
    actions.action_state,
    actions.mark_awaiting_evidence,
    # The French names stay registered: recorded histories and workflows in flight use them
    # (S34-02). Same implementation.
    actions.charger_l_action,  # lang: fr-ok
    actions.executer_l_effet,  # lang: fr-ok
    actions.compenser_l_effet,  # lang: fr-ok
    actions.cloturer_l_action,  # lang: fr-ok
    actions.proposer_l_action_de_transition,  # lang: fr-ok
    actions.etat_de_l_action,  # lang: fr-ok
    actions.marquer_l_attente,  # lang: fr-ok
    stage.prepare_stage,
    execution.start_run,
    execution.await_run,
    execution.cancel_run,
    suspension.suspend_run,
    suspension.release_suspended_run,
    suspension.resume_run,
    bilan.collect_spend,
    bilan.record_run_outcome,
    tracker.mirror_state,
    tracker.update_status_comment,
    tracker.create_human_request,
    tracker.close_human_request,
    tracker.notify,
    tracker.close_out,
    tracker.reconcile_tracker,
    schedules.fire_schedules,
    gates.evaluate_gates,
    gates.check_scope_violations,
    scm.open_pull_request,
    scm.enqueue_merge,
    scm.ensure_branch,
    scm.collect_run_artifacts,
    train.load_train_config,
    train.check_window,
    train.create_release,
    train.promote,
    train.run_smoke,
    train.soak,
    train.request_approval,
    train.promote_canary_step,
    train.verify_prod,
    train.finish_release,
    train.confirm_auto_sync,
    train.confirmer_auto_sync,  # lang: fr-ok
    train.rollback,
    train.mark_release,
    train.apply_terraform,
    train.train_stats,
    findings.triage_finding,
    findings.finding_quality_ratio,
    provisioning.load_template_steps,
    provisioning.run_provision_step,
    provisioning.finish_provisioning,
    memory.ingest_sources,
    memory.ingest_alert,
    memory.write_run_lesson,
    memory.accept_pending_facts,
    memory.memory_ab_report,
    evals.list_fixtures,
    evals.run_eval_cell,
    evals.publish_matrix,
]

__all__ = [
    "ALL_ACTIVITIES",
    "bilan",
    "evals",
    "execution",
    "findings",
    "gates",
    "memory",
    "provisioning",
    "schedules",
    "scm",
    "stage",
    "tracker",
    "train",
]
