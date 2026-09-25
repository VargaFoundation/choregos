"""Ce que l'interpréteur transmet aux activités d'une étape : le plan d'un run."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StagePlan:
    """Ce que l'interpréteur transmet aux activités pour préparer un run."""

    project_id: str
    work_item_id: str
    transition_id: str
    role: str
    from_state: str
    to_state: str
    actor: str
    attempt: int
    backend: str | None = None
    model_request: str = "profile:by_size"
    fresh_context: bool = False
    max_turns: int | None = None
    max_minutes: int | None = None
    playbook: str | None = None
    outputs: list[str] | None = None
    inputs: list[str] | None = None
