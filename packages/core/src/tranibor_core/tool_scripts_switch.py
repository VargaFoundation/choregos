# SPDX-License-Identifier: Apache-2.0
"""Whether an organisation's runs may be offered tool scripts at all (ADR 0122 §6, amended 2026-11-05;
S57-05b).

The organisation decides: its setting `tool_scripts` (a key of `Organization.settings`) is `enabled`,
`disabled`, or absent, and absent follows the deployment's default (`CHOREGOS_TOOL_SCRIPTS`, Helm
`choregos-orchestrator.runner.toolScripts.enabled`). The deployment keeps a hard brake,
`CHOREGOS_TOOL_SCRIPTS_ALLOWED` (Helm `…toolScripts.allowed`, on by default): off, no run of any
organisation is offered them, whatever its setting says. Past this switch, the organisation's ceiling
(which only forbids) and the project's policy (`tools.scripts: offer`, per role) decide as before.

The API writes the setting (`PATCH /orgs/{org}`, its administrator) and the orchestrator reads it with the
project's bundle: both go through this module, so they never disagree on its name or its values.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, get_args

#: The key of `Organization.settings` that holds the organisation's choice.
ORG_SETTING = "tool_scripts"
OrgToolScripts = Literal["enabled", "disabled"]
CHOICES: tuple[str, ...] = get_args(OrgToolScripts)


def org_tool_scripts(settings: Mapping[str, Any] | None) -> OrgToolScripts | None:
    """The organisation's choice, or `None` when it follows the deployment's default. A stored value
    outside the two choices reads as no choice: it never turns scripts on."""
    found = (settings or {}).get(ORG_SETTING)
    if found == "enabled":
        return "enabled"
    if found == "disabled":
        return "disabled"
    return None


def tool_scripts_refusal(
    choice: OrgToolScripts | None, *, deployment_default: bool, deployment_allowed: bool
) -> str | None:
    """Why the organisation's runs get no tool scripts, or `None` when the ceiling and the policy decide.
    The brake first, then the organisation's choice, then the deployment's default."""
    if not deployment_allowed:
        return (
            "the deployment's brake refuses tool scripts to every organisation "
            "(CHOREGOS_TOOL_SCRIPTS_ALLOWED)"
        )
    if choice == "disabled":
        return "the organisation has tool scripts disabled (its `tool_scripts` setting)"
    if choice is None and not deployment_default:
        return (
            "the organisation follows the deployment's default, which is off (CHOREGOS_TOOL_SCRIPTS); "
            "its administrator can enable them (`tool_scripts: enabled`)"
        )
    return None


def tool_scripts_switched_on(
    choice: OrgToolScripts | None, *, deployment_default: bool, deployment_allowed: bool
) -> bool:
    """`True` when the organisation's runs may be offered tool scripts, as far as the switch goes."""
    refusal = tool_scripts_refusal(
        choice, deployment_default=deployment_default, deployment_allowed=deployment_allowed
    )
    return refusal is None
