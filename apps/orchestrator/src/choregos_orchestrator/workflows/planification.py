# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal
"""Former module path of `choregos_orchestrator.workflows.scheduling` (ADR 0098 §3 (a)).

No `replace_module` here: the Temporal sandbox re-imports the modules of `workflows/` on its own.
`workflow` stays exported because a test patches `planification.workflow.execute_activity`.
"""

from __future__ import annotations

from temporalio import workflow

from .scheduling import execute_on_its_queue, start_on_its_queue

executer_activite = execute_on_its_queue
demarrer_activite = start_on_its_queue

__all__ = ["demarrer_activite", "executer_activite", "workflow"]
