# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal; former `services.routing`, ADR 0098
"""The former name of `choregos_api.services.routing`: the same module object, so a patch on one
is a patch on the other."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .routing import (
    Naissance,
    Routed,
    WorkItemBirth,
    _budget_at_launch,
    choisir_workflow,
    choose_workflow,
    new_trial_work_item,
    new_work_item,
    noter_les_etiquettes,
    nouveau_ticket,
    nouveau_ticket_d_essai,
    record_labels,
    refuse_reserved_fields,
    route,
    validate_fields,
    valider_les_champs,
    verifier_la_naissance,
    verify_birth,
    with_tracker,
)

__all__ = [
    "Naissance",
    "Routed",
    "WorkItemBirth",
    "_budget_at_launch",
    "choisir_workflow",
    "choose_workflow",
    "new_trial_work_item",
    "new_work_item",
    "noter_les_etiquettes",
    "nouveau_ticket",
    "nouveau_ticket_d_essai",
    "record_labels",
    "refuse_reserved_fields",
    "route",
    "validate_fields",
    "valider_les_champs",
    "verifier_la_naissance",
    "verify_birth",
    "with_tracker",
]

replace_module(__name__, sys.modules["choregos_api.services.routing"])
