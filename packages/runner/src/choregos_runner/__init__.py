"""Runner Choregos : client ACP headless, guardrails, boucle DoD, publication.

Le runner est jetable : il ne garde rien. Tout ce qu'il produit part par l'API interne
(journal, findings, résultat) et dans l'object store (transcript, rapports).
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
