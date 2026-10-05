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
from .definitions import (
    DEFAULT_POLICY_PRESET,
    DEFAULT_WORKFLOW,
    active_policy,
    active_workflow,
    default_workflow,
    ensure_defaults,
    policy_engine,
    policy_model,
    project_config,
    publier_workflow,
    workflow_actif,
    workflow_du_ticket,
    workflow_model,
    workflow_yaml,
)
from .evenements import (
    persist_event,
)
from .memoire import (
    MIN_TICKETS_PAR_GROUPE,
    _ab_verdict,
    _window_stats,
    memory_ab_comparison,
)
from .projets import (
    first_pass_merge_rate,
    project_dto,
    project_stats,
)
from .routage import (
    Naissance,
    choisir_workflow,
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
    totals_from,
    work_item_dto,
)

__all__ = [
    "DEFAULT_POLICY_PRESET",
    "DEFAULT_WORKFLOW",
    "DOCUMENTS_LOGICIELS",
    "MIN_TICKETS_PAR_GROUPE",
    "Naissance",
    "_ab_verdict",
    "_statut_temporal",
    "_window_stats",
    "active_policy",
    "active_workflow",
    "choisir_workflow",
    "chronologie",
    "cle_de_ticket_interne",
    "creer_un_ticket",
    "default_workflow",
    "ensure_defaults",
    "estimate_cost",
    "first_pass_merge_rate",
    "human_request_dto",
    "le_tracker_est_interne",
    "memory_ab_comparison",
    "nouveau_ticket",
    "persist_event",
    "policy_engine",
    "policy_model",
    "project_config",
    "project_dto",
    "project_stats",
    "publier_workflow",
    "ranger_les_sorties",
    "record_cost",
    "release_dto",
    "run_dto",
    "run_summary",
    "totals_from",
    "work_item_dto",
    "workflow_actif",
    "workflow_du_ticket",
    "workflow_model",
    "workflow_yaml",
]
