# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal
"""Former module path of `choregos_core.dsl.requirements` (ADR 0098 §3 (a))."""

from __future__ import annotations

from .requirements import (
    ORDER,
    ROLES_IN_A_REPOSITORY,
    ROLES_IN_PRODUCTION,
    Requirement,
    requirements,
)

Exigence = Requirement
ORDRE = ORDER
ROLES_DANS_UN_DEPOT = ROLES_IN_A_REPOSITORY
ROLES_EN_PRODUCTION = ROLES_IN_PRODUCTION
exigences = requirements

__all__ = ["ORDRE", "ROLES_DANS_UN_DEPOT", "ROLES_EN_PRODUCTION", "Exigence", "exigences"]
