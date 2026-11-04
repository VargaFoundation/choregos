# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal; former `services.connectors`, ADR 0098
"""The former name of `choregos_api.services.connectors`: the same module object, so a patch on one
is a patch on the other."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .connectors import (
    RANG,
    RANK,
    a_field_of,
    check_runtime_allowed,
    check_type_config,
    declared_operation,
    human_floor,
    plus_stricte,
    policy_for_project,
    politique_pour_le_projet,
    spec_du_type,
    spec_to_declare,
    stricter,
    type_spec,
    undeclared,
    verifier_les_secrets,
    verify_secrets,
)

__all__ = [
    "RANG",
    "RANK",
    "a_field_of",
    "check_runtime_allowed",
    "check_type_config",
    "declared_operation",
    "human_floor",
    "plus_stricte",
    "policy_for_project",
    "politique_pour_le_projet",
    "spec_du_type",
    "spec_to_declare",
    "stricter",
    "type_spec",
    "undeclared",
    "verifier_les_secrets",
    "verify_secrets",
]

replace_module(__name__, sys.modules["choregos_api.services.connectors"])
