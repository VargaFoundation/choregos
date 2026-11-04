# SPDX-License-Identifier: Apache-2.0
"""The context reduction (ADR 0055) and the tool scripts (ADR 0122) a run starts with.

Three switches each, the strictest wins: the deployment (`CHOREGOS_CONTEXT_REDUCTION`, Helm
`runner.contextReduction.enabled`; `CHOREGOS_TOOL_SCRIPTS`, Helm `runner.toolScripts.enabled`) can
refuse it whatever the policy says; the organisation's ceiling can forbid tool scripts (the policy
the orchestrator reads is already brought down to it); the policy turns it on, per role if it wants.
The stage input carries the result, so a run says what it ran with.
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import ContextPolicy, StageContext, StageInput, ToolScripts, ToolsPolicy


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


def resolve_tool_scripts(
    policy_tools: ToolsPolicy, role: str, *, allowed_by_deployment: bool
) -> ToolScripts | None:
    """Whether this role's agent may call its tools from a script, and how many calls at once:
    `None` (off) when the deployment refuses, or when the policy does not offer it to the role."""
    if not allowed_by_deployment:
        return None
    return policy_tools.for_role(role)


def stage_input_payload(stage_input: StageInput) -> dict[str, Any]:
    """The stage input as the runner reads it. A setting that is off is left out: a runner older
    than ADR 0055 refuses an unknown `context`, one older than ADR 0122 an unknown `tools.scripts`,
    and an absent key means the same as off."""
    exclude: dict[str, Any] = {}
    if not stage_input.context.active:
        exclude["context"] = True
    if stage_input.tools.scripts is None:
        exclude["tools"] = {"scripts": True}
    return stage_input.model_dump(mode="json", by_alias=True, exclude=exclude or None)
