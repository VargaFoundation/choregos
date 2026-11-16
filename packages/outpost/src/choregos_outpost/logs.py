# SPDX-License-Identifier: Apache-2.0
"""The daemon's own log: JSON lines on standard output, with no secret in them.

It is not the audit log (`audit.py`), which is the operator's record of each assignment. This one
says what the daemon is doing: it started, the door refused it, an endpoint is unreachable.

Every string of a line goes through `redact_known`: the credentials the daemon read to open its
endpoints are remembered when they are read (`toolhost.py`), and masked wherever a line would
carry one, an error text of a server that echoes its `Authorization` header for instance. A
private key is never handed to a logger in the first place (`identity.py` exposes none).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from choregos_core.redaction import redact_known


def mask_known_secrets(_logger: Any, _method: str, event: MutableMapping[str, Any]) -> dict[str, Any]:
    """Masks, in every string of a log line, the credentials this process read."""

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            return redact_known(value)
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [clean(item) for item in value]
        return value

    return {key: clean(value) for key, value in event.items()}


def configure(level: str = "INFO") -> None:
    """JSON lines on standard output, one per event, with its level and its time (UTC)."""
    threshold = getattr(logging, level.upper(), logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            mask_known_secrets,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(threshold),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )


def get_logger() -> Any:
    return structlog.get_logger("choregos.outpost")
