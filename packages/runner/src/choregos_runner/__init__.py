# SPDX-License-Identifier: Apache-2.0
"""Runner Choregos : client ACP headless, guardrails, boucle DoD, publication.

The runner is disposable: it keeps nothing. Everything it produces leaves through the internal API
(journal, findings, result) and through the object store (transcript, reports).
"""

from __future__ import annotations

from .client import InternalApiError, InternalClient
from .config import RunnerSettings, get_settings
from .exits import Exit
from .guardrails import Decision, GuardRails
from .runner import Runner, RunOutcome, run_stage

__version__ = "0.1.0"

__all__ = [
    "Decision",
    "Exit",
    "GuardRails",
    "InternalApiError",
    "InternalClient",
    "RunOutcome",
    "Runner",
    "RunnerSettings",
    "get_settings",
    "run_stage",
]
