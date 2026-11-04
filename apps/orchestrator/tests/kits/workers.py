"""Workers for the interpreter tests: one per queue, with a few activities replaced."""

from __future__ import annotations

from typing import Any


def worker_with(temporal_env: Any, replacements: dict[str, Any]) -> Any:
    """Workers spread by queue, where a few activities are replaced by their double.

    The keys are **Temporal names**: the name declared by `@activity.defn(name=...)` decides the
    queue, so a double of `await_run` registers on `executor`, where the workflow schedules it.
    """
    from choregos_orchestrator.activities import ALL_ACTIVITIES
    from choregos_orchestrator.testing import workers_by_queue
    from choregos_orchestrator.workflows import ALL_WORKFLOWS, WORKFLOW_ACTIVITIES

    return workers_by_queue(
        temporal_env.client,
        activities=[*ALL_ACTIVITIES, *WORKFLOW_ACTIVITIES],
        workflows=ALL_WORKFLOWS,
        replacements=replacements,
    )
