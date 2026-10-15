# SPDX-License-Identifier: Apache-2.0
"""Ce que l'interpréteur transmet aux activités d'une étape : le plan d'un run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
    #: The declared type of each output (ADR 0049).
    output_types: dict[str, str] | None = None
    #: Un agent du registre (ADR 0033) : `slug` ou `slug@version`.
    agent: str | None = None
    #: A trial (S32-03): the draft version to run, unpublished — it stands in for a registry agent —
    #: and the budget cap of the trial, which only tightens the run's.
    trial_spec: dict[str, Any] | None = None
    trial_budget_usd: float | None = None
    #: A job step (ADR 0056): `{"command": [...], "image": name | None}`; no model, no key.
    job: dict[str, Any] | None = None
    #: An eval cell's run (S37-10, ADR 0055): `context` (the cell's reduction, still under the
    #: deployment's kill switch), `base_branch` (the toy repository's branch) and `facts` (the
    #: ticket's assertions, measured by the runner). Nothing else ever sets it.
    eval_cell: dict[str, Any] | None = None
