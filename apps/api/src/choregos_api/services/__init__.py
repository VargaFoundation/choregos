# SPDX-License-Identifier: Apache-2.0
"""Services partagés par les routeurs : requêtes, conversions, effets de bord.

Les routeurs restent minces ; toute la logique réutilisable (par le CLI, l'orchestrateur
ou les webhooks) vit ici — en six modules, un par sujet, réexportés pour que
`choregos_api.services.X` reste l'adresse de tout.
"""

from __future__ import annotations

from .couts import (
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
    default_workflow,
    effective_policy,
    effective_policy_and_clamps,
    ensure_defaults,
    policy_engine,
    policy_model,
    project_config,
    publier_workflow,
    signaler_ce_que_la_politique_n_applique_pas,
    workflow_actif,
    workflow_du_ticket,
    workflow_model,
    workflow_yaml,
)
from .evenements import (
    persist_event,
)
from .human_requests import new_human_request
from .memoire import (
    MIN_TICKETS_PAR_GROUPE,
    _ab_verdict,
    _window_stats,
    memory_ab_comparison,
)
from .parcours import arete_du_deplacement, parcours
from .projets import (
    first_pass_merge_rate,
    project_dto,
    project_stats,
)
from .routage import (
    Naissance,
    choisir_workflow,
    noter_les_etiquettes,
    nouveau_ticket,
)
from .tickets import (
    DOCUMENTS_LOGICIELS,
    _statut_temporal,
    chronologie,
    cle_de_ticket_interne,
    creer_un_ticket,
    human_request_dto,
    le_tracker_est_interne,
    ranger_les_sorties,
    release_dto,
    run_dto,
    run_summary,
    tool_connectors_of,
    totals_from,
    work_item_dto,
)

__all__ = [
    "DEFAULT_POLICY_PRESET",
    "DEFAULT_WORKFLOW",
    "DOCUMENTS_LOGICIELS",
    "MIN_TICKETS_PAR_GROUPE",
    "DecidedBy",
    "Naissance",
    "_ab_verdict",
    "_statut_temporal",
    "_window_stats",
    "active_policy",
    "active_workflow",
    "arete_du_deplacement",
    "choisir_workflow",
    "chronologie",
    "cle_de_ticket_interne",
    "creer_un_ticket",
    "default_workflow",
    "effective_policy",
    "effective_policy_and_clamps",
    "ensure_defaults",
    "estimate_cost",
    "first_pass_merge_rate",
    "human_request_dto",
    "le_tracker_est_interne",
    "memory_ab_comparison",
    "new_human_request",
    "noter_les_etiquettes",
    "nouveau_ticket",
    "parcours",
    "persist_event",
    "policy_engine",
    "policy_model",
    "project_config",
    "project_dto",
    "project_stats",
    "publier_workflow",
    "ranger_les_sorties",
    "record_cost",
    "record_decisions",
    "record_human_verdict",
    "record_reopened",
    "record_terminal_outcome",
    "record_transition_taken",
    "release_dto",
    "run_dto",
    "run_summary",
    "signaler_ce_que_la_politique_n_applique_pas",
    "tool_connectors_of",
    "totals_from",
    "work_item_dto",
    "workflow_actif",
    "workflow_du_ticket",
    "workflow_model",
    "workflow_yaml",
]
