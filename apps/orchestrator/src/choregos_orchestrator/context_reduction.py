# SPDX-License-Identifier: Apache-2.0
"""The context reduction a run starts with (ADR 0055).

Three switches, the strictest wins: the deployment (`CHOREGOS_CONTEXT_REDUCTION`, Helm
`runner.contextReduction.enabled`) can refuse it whatever the policy says; the policy turns it on,
per role if it wants; the stage input carries the result, so a run says what it ran with.
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import ContextPolicy, StageContext, StageInput


def resolve_context(policy: ContextPolicy, role: str, *, allowed_by_deployment: bool) -> StageContext:
    """The policy's values for this role, or everything off when the deployment refuses."""
    if not allowed_by_deployment:
        return StageContext()
    return policy.for_role(role)


def forced_context(values: dict[str, Any], *, allowed_by_deployment: bool) -> StageContext:
    """An eval cell's reduction (S37-10): the cell decides instead of the policy, never instead of
    the deployment."""
    if not allowed_by_deployment:
        return StageContext()
    return StageContext.model_validate(values)


def stage_input_payload(stage_input: StageInput) -> dict[str, Any]:
    """The stage input as the runner reads it. `context` is left out when nothing is reduced: a
    runner older than ADR 0055 refuses an unknown key, and an absent `context` means the same."""
    exclude = None if stage_input.context.active else {"context"}
    return stage_input.model_dump(mode="json", by_alias=True, exclude=exclude)
