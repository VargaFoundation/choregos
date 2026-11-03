# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal
"""Former module path of `choregos_orchestrator.queues` (ADR 0098 §3 (a))."""

from __future__ import annotations

from .queues import ACTIVITIES_BY_QUEUE, QUEUE_BY_ACTIVITY, activity_name, queue_of

ACTIVITES_PAR_FILE = ACTIVITIES_BY_QUEUE
FILE_PAR_ACTIVITE = QUEUE_BY_ACTIVITY
nom_de_l_activite = activity_name
file_de = queue_of

__all__ = ["ACTIVITES_PAR_FILE", "FILE_PAR_ACTIVITE", "file_de", "nom_de_l_activite"]
