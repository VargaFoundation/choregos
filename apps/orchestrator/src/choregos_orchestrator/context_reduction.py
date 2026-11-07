# SPDX-License-Identifier: Apache-2.0
"""The context reduction (ADR 0055) and the tool scripts (ADR 0122) a run starts with.

Context reduction: the deployment (`CHOREGOS_CONTEXT_REDUCTION`, Helm `runner.contextReduction.enabled`)
can refuse it whatever the policy says. Tool scripts, strictest first: the organisation's switch
(`tool_scripts_switched_on`: the deployment's brake `CHOREGOS_TOOL_SCRIPTS_ALLOWED`, then its `tool_scripts`
setting, else the deployment's default `CHOREGOS_TOOL_SCRIPTS`; S57-05b); the organisation's ceiling,
which can forbid them; what the policy says for the role (its `per_role` override, else its
`tools.scripts`); the run's agent's default (its catalogue entry's `spec.tools.scripts`, ADR 0122 §9,
S57-15); else off. The stage input carries the result, so a run says what it ran with.
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import ContextPolicy, StageContext, StageInput, ToolScripts, ToolsPolicy
from choregos_contracts.policy import ToolScriptsMode
from choregos_core.policy_ceiling import PolicyCeiling, forbids_tool_scripts
from choregos_core.tool_scripts_switch import OrgToolScripts, tool_scripts_switched_on

from .config import OrchestratorSettings


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


def tool_scripts_on_for(choice: OrgToolScripts | None, settings: OrchestratorSettings) -> bool:
    """The organisation's switch for tool scripts (S57-05b): its choice, else the deployment's default,
    never past the deployment's brake."""
    return tool_scripts_switched_on(
        choice, deployment_default=settings.tool_scripts, deployment_allowed=settings.tool_scripts_allowed
    )


def resolve_tool_scripts(
    policy_tools: ToolsPolicy,
    role: str,
    *,
    switched_on: bool,
    ceiling: PolicyCeiling | None,
    agent_default: ToolScriptsMode | None,
) -> ToolScripts | None:
    """Whether this role's agent may call its tools from a script, and how many calls at once (the
    policy's `max_concurrent_calls`, its role's if it sets one). `None` (off) when the organisation's switch
    is off (`tool_scripts_on_for`) or its ceiling forbids them; otherwise what the policy says for the role
    decides, and when it says nothing, the agent's default (S57-15), else off. The ceiling is asked here
    and not only through the clamped policy: a policy that says nothing is not clamped."""
    if not switched_on or forbids_tool_scripts(ceiling):
        return None
    return policy_tools.for_role(role, default=agent_default)


def forced_tool_scripts(values: dict[str, Any] | None, *, switched_on: bool) -> ToolScripts | None:
    """An eval cell's door (S57-09): `{max_concurrent_calls}` in a `scripts` cell, `None` (the model's MCP
    tools only) in an `mcp` cell, or for a plan written before the cell had one. The cell decides instead
    of the policy, never instead of the organisation's switch or the deployment's brake."""
    if values is None or not switched_on:
        return None
    return ToolScripts.model_validate(values)


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
