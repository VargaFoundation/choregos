"""Activités de l'orchestrateur : tout ce qui touche au monde extérieur.

Elles sont idempotentes, typées, et n'appellent jamais un modèle directement.
"""

from __future__ import annotations

from . import evals, findings, gates, memory, provisioning, scm, stage, tracker, train

ALL_ACTIVITIES = [
    stage.prepare_stage,
    stage.start_run,
    stage.await_run,
    stage.cancel_run,
    stage.collect_spend,
    stage.record_run_outcome,
    tracker.mirror_state,
    tracker.update_status_comment,
    tracker.create_human_request,
    tracker.close_human_request,
    tracker.notify,
    tracker.close_out,
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
    train.rollback,
    train.mark_release,
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
    evals.list_fixtures,
    evals.run_eval_cell,
    evals.publish_matrix,
]

__all__ = [
    "ALL_ACTIVITIES",
    "evals",
    "findings",
    "gates",
    "memory",
    "provisioning",
    "scm",
    "stage",
    "tracker",
    "train",
]
