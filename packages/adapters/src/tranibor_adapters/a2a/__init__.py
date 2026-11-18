# SPDX-License-Identifier: Apache-2.0
"""A2A (Agent2Agent) client: the Agent Card and the four calls of a step (ADR 0062)."""

from .card import (
    AUTH_BEARER,
    AUTH_NONE,
    AUTH_OAUTH,
    AgentCard,
    CardError,
    CardSkill,
    digest_of,
    fetch_card,
    parse_card,
)
from .client import (
    A2aArtifact,
    A2aAuthRequired,
    A2aClient,
    A2aError,
    A2aEvent,
    A2aMethodNotSupported,
    A2aPart,
    A2aTask,
    file_part,
    text_part,
)

__all__ = [
    "AUTH_BEARER",
    "AUTH_NONE",
    "AUTH_OAUTH",
    "A2aArtifact",
    "A2aAuthRequired",
    "A2aClient",
    "A2aError",
    "A2aEvent",
    "A2aMethodNotSupported",
    "A2aPart",
    "A2aTask",
    "AgentCard",
    "CardError",
    "CardSkill",
    "digest_of",
    "fetch_card",
    "file_part",
    "parse_card",
    "text_part",
]
