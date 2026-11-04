# SPDX-License-Identifier: Apache-2.0
"""Services shared by the routers: queries, conversions, side effects.

The routers stay thin; all the logic that can be reused (by the CLI, the orchestrator or the
webhooks) lives here, in one module per subject, re-exported so that `choregos_api.services.X`
stays the address of everything.
"""

from __future__ import annotations

from .costs import (
    estimate_cost,
    record_cost,
)
from .decision_ledger import (
    DecidedBy,
    record_decisions,
    record_human_verdict,
    record_reopened,
    record_terminal_outcome,
    record_transition_taken,
)
from .definitions import (
    DEFAULT_POLICY_PRESET,
    DEFAULT_WORKFLOW,
    active_policy,
    active_workflow,
    active_workflow_named,
    default_workflow,
    effective_policy,
    effective_policy_and_clamps,
    ensure_defaults,
    policy_engine,
    policy_model,
    project_config,
    publier_workflow,  # lang: fr-ok alias:internal
    publish_workflow,
    report_unenforced_policy,
    work_item_workflow,
    workflow_model,
    workflow_yaml,
)
from .events import (
    persist_event,
)
from .human_requests import new_human_request
from .journey import journey, move_edge
from .memory import (
    MIN_WORK_ITEMS_PER_GROUP,
    _ab_verdict,
    _window_stats,
    memory_ab_comparison,
)
from .projects import (
    first_pass_merge_rate,
    project_dto,
    project_stats,
)
from .routing import (
    WorkItemBirth,
    choose_workflow,
    new_work_item,
    record_labels,
)
from .tickets import (
    SOFTWARE_DOCUMENTS,
    _temporal_status,
    create_work_item,
    human_request_dto,
    internal_work_item_key,
    outputs_to_store,
    release_dto,
    run_dto,
    run_summary,
    store_outputs,
    timeline,
    tool_connectors_of,
    totals_from,
    tracker_is_internal,
    work_item_dto,
)

# Former names (ADR 0098 §3 (b)): the importers switch story by story.
DOCUMENTS_LOGICIELS = SOFTWARE_DOCUMENTS  # lang: fr-ok alias:internal
MIN_TICKETS_PAR_GROUPE = MIN_WORK_ITEMS_PER_GROUP  # lang: fr-ok alias:internal
Naissance = WorkItemBirth  # lang: fr-ok alias:internal
_statut_temporal = _temporal_status  # lang: fr-ok alias:internal
arete_du_deplacement = move_edge  # lang: fr-ok alias:internal
choisir_workflow = choose_workflow  # lang: fr-ok alias:internal
chronologie = timeline  # lang: fr-ok alias:internal
cle_de_ticket_interne = internal_work_item_key  # lang: fr-ok alias:internal
creer_un_ticket = create_work_item  # lang: fr-ok alias:internal
le_tracker_est_interne = tracker_is_internal  # lang: fr-ok alias:internal
noter_les_etiquettes = record_labels  # lang: fr-ok alias:internal
nouveau_ticket = new_work_item  # lang: fr-ok alias:internal
parcours = journey  # lang: fr-ok alias:internal
ranger_les_sorties = store_outputs  # lang: fr-ok alias:internal
signaler_ce_que_la_politique_n_applique_pas = report_unenforced_policy  # lang: fr-ok alias:internal
workflow_actif = active_workflow_named  # lang: fr-ok alias:internal
workflow_du_ticket = work_item_workflow  # lang: fr-ok alias:internal

__all__ = [
    "DEFAULT_POLICY_PRESET",
    "DEFAULT_WORKFLOW",
    "DOCUMENTS_LOGICIELS",  # lang: fr-ok alias:internal
    "MIN_TICKETS_PAR_GROUPE",  # lang: fr-ok alias:internal
    "MIN_WORK_ITEMS_PER_GROUP",
    "SOFTWARE_DOCUMENTS",
    "DecidedBy",
    "Naissance",  # lang: fr-ok alias:internal
    "WorkItemBirth",
    "_ab_verdict",
    "_statut_temporal",  # lang: fr-ok alias:internal
    "_temporal_status",
    "_window_stats",
    "active_policy",
    "active_workflow",
    "active_workflow_named",
    "arete_du_deplacement",  # lang: fr-ok alias:internal
    "choisir_workflow",  # lang: fr-ok alias:internal
    "choose_workflow",
    "chronologie",  # lang: fr-ok alias:internal
    "cle_de_ticket_interne",  # lang: fr-ok alias:internal
    "create_work_item",
    "creer_un_ticket",  # lang: fr-ok alias:internal
    "default_workflow",
    "effective_policy",
    "effective_policy_and_clamps",
    "ensure_defaults",
    "estimate_cost",
    "first_pass_merge_rate",
    "human_request_dto",
    "internal_work_item_key",
    "journey",
    "le_tracker_est_interne",  # lang: fr-ok alias:internal
    "memory_ab_comparison",
    "move_edge",
    "new_human_request",
    "new_work_item",
    "noter_les_etiquettes",  # lang: fr-ok alias:internal
    "nouveau_ticket",  # lang: fr-ok alias:internal
    "outputs_to_store",
    "parcours",  # lang: fr-ok alias:internal
    "persist_event",
    "policy_engine",
    "policy_model",
    "project_config",
    "project_dto",
    "project_stats",
    "publier_workflow",  # lang: fr-ok alias:internal
    "publish_workflow",
    "ranger_les_sorties",  # lang: fr-ok alias:internal
    "record_cost",
    "record_decisions",
    "record_human_verdict",
    "record_labels",
    "record_reopened",
    "record_terminal_outcome",
    "record_transition_taken",
    "release_dto",
    "report_unenforced_policy",
    "run_dto",
    "run_summary",
    "signaler_ce_que_la_politique_n_applique_pas",  # lang: fr-ok alias:internal
    "store_outputs",
    "timeline",
    "tool_connectors_of",
    "totals_from",
    "tracker_is_internal",
    "work_item_dto",
    "work_item_workflow",
    "workflow_actif",  # lang: fr-ok alias:internal
    "workflow_du_ticket",  # lang: fr-ok alias:internal
    "workflow_model",
    "workflow_yaml",
]
