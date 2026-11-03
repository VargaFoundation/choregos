# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal; former `schemas.delivery`, ADR 0098
"""The former name of `choregos_api.schemas.delivery`: every name still resolves."""

from __future__ import annotations

from choregos_core.compat import replace_module

from . import delivery as _current
from .delivery import (
    AbortRequest,
    ApproveRequest,
    FindingAck,
    FindingAction,
    FindingDto,
    FindingPage,
    FreezeRequest,
    ReleaseDto,
    ReleaseItemDto,
    ReleasePage,
    TrainStatusDto,
)

__all__ = [
    "AbortRequest",
    "ApproveRequest",
    "FindingAck",
    "FindingAction",
    "FindingDto",
    "FindingPage",
    "FreezeRequest",
    "ReleaseDto",
    "ReleaseItemDto",
    "ReleasePage",
    "TrainStatusDto",
]

replace_module(__name__, _current)
