# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal; former `schemas.platform`, ADR 0098
"""The former name of `choregos_api.schemas.platform`: every name still resolves."""

from __future__ import annotations

from choregos_core.compat import replace_module

from . import platform as _current
from .platform import (
    AgentBackendInfo,
    AgentBackendUpdate,
    AuditEntry,
    AuditPage,
    ConformanceReport,
    ExecutorInfo,
    GatewayKeyInfo,
    PlatformIdentity,
    PlatformIdentityRow,
    WebhookAck,
)

__all__ = [
    "AgentBackendInfo",
    "AgentBackendUpdate",
    "AuditEntry",
    "AuditPage",
    "ConformanceReport",
    "ExecutorInfo",
    "GatewayKeyInfo",
    "PlatformIdentity",
    "PlatformIdentityRow",
    "WebhookAck",
]

replace_module(__name__, _current)
