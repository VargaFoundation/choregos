# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal; former `schemas.measures`, ADR 0098
"""The former name of `choregos_api.schemas.measures`: every name still resolves."""

from __future__ import annotations

from choregos_core.compat import replace_module

from . import measures as _current
from .measures import (
    BudgetAlertDto,
    CostReport,
    CostRow,
    CostTotal,
    CrossBackendArm,
    CrossBackendReport,
    DoraMetric,
    DoraReport,
    MemoryAbGroup,
    MemoryAbReport,
    MemoryDecision,
    MemoryDto,
    MemoryReimport,
)

__all__ = [
    "BudgetAlertDto",
    "CostReport",
    "CostRow",
    "CostTotal",
    "CrossBackendArm",
    "CrossBackendReport",
    "DoraMetric",
    "DoraReport",
    "MemoryAbGroup",
    "MemoryAbReport",
    "MemoryDecision",
    "MemoryDto",
    "MemoryReimport",
]

replace_module(__name__, _current)
