# SPDX-License-Identifier: Apache-2.0
"""Journalisation JSON structurée (clés `project`, `work_item`, `run_id`, `stage`)."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def redact_known_secrets(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Mask, in every string of a log line, the secret values this process resolved (ADR 0058):
    a connector secret that an exception text or a debug line would carry out of the process."""
    from choregos_core.redaction import redact_known

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            return redact_known(value)
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return type(value)(clean(v) for v in value)
        return value

    return {k: clean(v) for k, v in event_dict.items()}


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO)
    )
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_known_secrets,
    ]
    processors.append(structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "choregos") -> Any:
    return structlog.get_logger(name)


def bind(**kwargs: Any) -> None:
    """Attache des clés au contexte de journalisation de la requête en cours."""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear() -> None:
    structlog.contextvars.clear_contextvars()
