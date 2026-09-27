# SPDX-License-Identifier: Apache-2.0
"""Planifier une activité **sur sa file** (docs/plan/02 §2.1).

`workflow.execute_activity` sans `task_queue` planifie sur la file du *workflow*. Tous les
workflows tournant sur `orchestrator`, toutes les activités y tournaient aussi, quelle que soit la
file que `repartition` leur attribuait. Cette enveloppe lit la table et pose le `task_queue` que
l'appel ne dit pas — un appelant qui le précise garde le dernier mot.
"""

from __future__ import annotations

from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from ..repartition import file_de

#: Sentinelle : `execute_activity` distingue « aucun argument » de « l'argument `None` », et une
#: activité sans paramètre existe (`train_stats`, `list_fixtures`).
_ABSENT = object()


def executer_activite(activite: Any, arg: Any = _ABSENT, **options: Any) -> Any:
    """Comme `workflow.execute_activity`, mais sur la file déclarée de l'activité.

    Retourne ce que retourne Temporal — un awaitable — donc s'utilise avec `await`, exactement
    comme l'appel qu'elle remplace.
    """
    if "task_queue" not in options:
        file = file_de(activite)
        if file is not None:
            options["task_queue"] = file
    if arg is _ABSENT:
        return workflow.execute_activity(activite, **options)
    return workflow.execute_activity(activite, arg, **options)
