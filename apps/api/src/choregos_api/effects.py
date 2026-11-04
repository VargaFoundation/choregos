# SPDX-License-Identifier: Apache-2.0
"""The effects of governed actions (ADR 0035), one seam: `declare_effect`.

An effect writes into a system that is not ours: a directory, a device manager, a provider's MCP
server. It is IDEMPOTENT by construction, because an action resumes after a failure: creating an
account looks it up first, adding to a group counts "already a member" as a success. It never runs
in the request that approves the action: the `ActionWorkflow` plays it, under its key, and
compensates it backwards if a later one fails.

The core ships `connector.call`, an operation of an organisation connector (ADR 0034), and `check`
(alias `verifier`), a control with no effect outside; an extension declares its own. An effect's
parameters are rendered in isolated Jinja, with those of the action (`params`) and the results of
the effects already done (`effects`): an onboarding's second effect adds to the groups the account
the first one created.

The French names this module had before 0.21 (`effets.py`, `declarer_un_effet`...) keep working
until 0.23.0 and warn (ADR 0098 §3 (f)).
"""

from __future__ import annotations

import warnings
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from choregos_core.compat import EXTERNAL_ALIAS_REMOVAL, alias_names
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db.models import Action, ConnectorOperation, OrgConnector, Project
from .services.mcp_gateway import route_of
from .services.mcp_gateway_identity import GatewayRefusal
from .services.platform_network import PlatformNetworkRefused, enforce_connector_call


class EffectRefused(Exception):  # noqa: N818 - a final refusal, answered as it is
    """A refusal that no new attempt will change (an account outside the unit, a forbidden
    operation, a missing parameter): the action fails and compensates."""


@dataclass(frozen=True)
class EffectContext:
    session: AsyncSession
    action: Action
    project: Project


Execute = Callable[[EffectContext, dict[str, Any]], Awaitable[dict[str, Any]]]

_EFFECTS: dict[str, Execute] = {}
#: The policy of each extension effect (ADR 0034): `connector.call` has none, it is the CALLED
#: OPERATION that has one, in the organisation and the project.
_POLICIES: dict[str, str] = {}


def declare_effect(
    name: str,
    execute: Execute,
    *,
    policy: str = "approval",
    politique: str | None = None,  # lang: fr-ok alias:external
) -> None:
    """Called by an extension when it loads. The same effect declared again passes; another one does not.

    `policy`: what a workflow action needs to play it: `approval` (a person decides, the default),
    `allowed` (the transition is enough) or `forbidden`. `politique=` is the former name of
    `policy=`: it keeps working until 0.23.0 and warns."""
    from choregos_adapters import POLICIES

    if politique is not None:  # lang: fr-ok alias:external
        warnings.warn(
            "declare_effect(politique=...) is deprecated: use policy= "  # lang: fr-ok alias:external
            f"(removed in {EXTERNAL_ALIAS_REMOVAL}, ADR 0098)",
            DeprecationWarning,
            stacklevel=2,
        )
        policy = politique  # lang: fr-ok alias:external
    if policy not in POLICIES:
        raise ValueError(f"unknown policy for the effect {name}: {policy} (known: {POLICIES})")
    if name in _EFFECTS and _EFFECTS[name] is not execute:
        raise ValueError(f"effect already declared: {name}")
    _EFFECTS[name] = execute
    _POLICIES[name] = policy


def effect_policy(name: str) -> str:
    """What an extension effect requires; an unknown effect is forbidden."""
    return _POLICIES.get(name, "forbidden")


def effect(name: str) -> Execute:
    try:
        return _EFFECTS[name]
    except KeyError:
        raise EffectRefused(f"unknown effect: {name} (known: {', '.join(sorted(_EFFECTS))})") from None


def declared_effects() -> frozenset[str]:
    return frozenset(_EFFECTS)


def render(value: Any, context: dict[str, Any]) -> Any:
    """The `{{ ... }}` strings rendered in ISOLATED Jinja, recursively; a missing variable is an
    error, not an empty string: an account without a UPN must not leave."""
    from jinja2 import StrictUndefined, TemplateError, UndefinedError
    from jinja2.sandbox import SandboxedEnvironment

    if isinstance(value, str):
        if "{{" not in value and "{%" not in value:
            return value
        try:
            return SandboxedEnvironment(undefined=StrictUndefined).from_string(value).render(**context)
        except UndefinedError as missing:
            raise EffectRefused(f"parameter not found: {missing}") from missing
        except TemplateError as refusal:  # a wrong syntax, an exit from the sandbox
            raise EffectRefused(f"template refused: {refusal}") from refusal
    if isinstance(value, dict):
        return {key: render(v, context) for key, v in value.items()}
    if isinstance(value, list):
        return [render(v, context) for v in value]
    return value


async def _connector_call(ctx: EffectContext, params: dict[str, Any]) -> dict[str, Any]:
    """`connector.call` under the organisation's egress allowlist (S39-22): a governed action runs
    for the workers, and a host the organisation has not opened is refused before it is called."""
    from .services.egress import egress_scope_for_org

    async with egress_scope_for_org(
        ctx.session, ctx.project.org_id, "workers", f"connector:{params.get('connector', '')}"
    ):
        return await _call_the_connector(ctx, params)


async def _within_the_network_bounds(ctx: EffectContext, instance: OrgConnector, label: str) -> None:
    """The organisation's network bounds are read again at the moment of the call (ADR 0070): they
    may have been tightened since the action was approved. A refusal is final, the action compensates."""
    # ADR 0105: the route first (mode `required`, `off`, no gateway: final), then the host it reaches.
    try:
        route = await route_of(ctx.session, instance)
    except GatewayRefusal as refusal:
        raise EffectRefused(f"{label} : {refusal}") from refusal
    try:
        await enforce_connector_call(
            ctx.session, instance, project=ctx.project, polite=False, host=route.host
        )
    except PlatformNetworkRefused as refusal:
        raise EffectRefused(f"{label} : {refusal.message()}") from refusal


async def _call_the_connector(ctx: EffectContext, params: dict[str, Any]) -> dict[str, Any]:
    """`connector.call`: an operation of an organisation connector, key resolved HERE.

    The action was approved: an `approval` operation therefore runs; a `forbidden` one does not."""
    import httpx
    from choregos_adapters.errors import AdapterError, UpstreamError
    from choregos_adapters.mcp import McpError
    from choregos_contracts import ActionStatus
    from choregos_core.egress import EgressRefusedError

    name, operation = str(params.get("connector", "")), str(params.get("operation", ""))
    arguments = dict(params.get("arguments") or {})
    # An action nobody approved writes nothing (ADR 0035, ADR 0052): the workflow is started by the
    # decision, but a workflow started by anything else must not find a way to the device.
    approved = {ActionStatus.APPROVED.value, ActionStatus.RUNNING.value, ActionStatus.AWAITING_EVIDENCE.value}
    if ctx.action.status not in approved:
        raise EffectRefused(
            f"{name}/{operation}: the action is {ctx.action.status}, not approved — nothing is done"
        )
    instance = (
        await ctx.session.execute(
            select(OrgConnector).where(OrgConnector.org_id == ctx.project.org_id, OrgConnector.name == name)
        )
    ).scalar_one_or_none()
    if instance is None:
        raise EffectRefused(f"no connector {name} in the organisation")
    row = (
        await ctx.session.execute(
            select(ConnectorOperation).where(
                ConnectorOperation.connector_id == instance.id, ConnectorOperation.name == operation
            )
        )
    ).scalar_one_or_none()
    from .services.connectors import policy_for_project
    from .services.mcp_gateway import connector_client
    from .services.mcp_gateway_identity import identity_of_action

    # The PROJECT's: the organisation's, tightened by the project, bounded by its groups. An approved
    # action plays an `approval` operation; an operation this project forbids itself, never.
    if row is None or await policy_for_project(ctx.session, ctx.project, name, operation) == "forbidden":
        raise EffectRefused(f"{name}/{operation} is forbidden to this project, or unknown")
    if row.input_schema:
        import jsonschema

        try:
            jsonschema.validate(arguments, row.input_schema)
        except jsonschema.ValidationError as exc:
            raise EffectRefused(f"{name}/{operation} : {exc.message}") from exc
    arguments.update(await _platform_arguments(ctx, instance, operation, arguments))
    await _within_the_network_bounds(ctx, instance, f"{name}/{operation}")
    try:
        # A gateway connector (ADR 0105) goes through the gateway, signed with the action's identity and
        # its approvers; a plugin's server (ADR 0096), its Service; any other, its type's adapter. A
        # `GatewayRefusal` is an `AdapterError`: the clause below makes it a final refusal, no retry.
        client: Any = await connector_client(
            ctx.session,
            instance,
            lambda: identity_of_action(ctx.session, ctx.action, ctx.project, instance, operation),
        )
        if hasattr(client, "call_tool"):
            result = await client.call_tool(operation, arguments)
            if result.get("isError"):
                raise EffectRefused(f"{name}/{operation}: the server answered with an error")
            # What the tool STRUCTURED, when it does: a following effect cites `effects[0].serial`
            # as for a typed connector, not the protocol's envelope (S20-07).
            structure = result.get("structuredContent")
            return dict(structure) if isinstance(structure, dict) else dict(result)
        return dict(await client.execute(operation, arguments))
    except UpstreamError as failure:
        code = failure.status_code or 0
        if code in {0, 429} or code >= 500:
            raise  # transient: Temporal retries
        raise EffectRefused(f"{name}/{operation} : {failure}") from failure
    except McpError as error:
        if error.code in {401, 403}:
            raise EffectRefused(f"{name}/{operation} : {error}") from error
        raise
    except EgressRefusedError as refusal:
        raise EffectRefused(f"{name}/{operation} : {refusal}") from refusal  # permanent: no retry
    except httpx.HTTPError:
        raise
    except AdapterError as refusal:
        raise EffectRefused(f"{name}/{operation} : {refusal}") from refusal


async def _platform_arguments(
    ctx: EffectContext, instance: OrgConnector, operation: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """What the operation's type asks the PLATFORM to fill (ADR 0052), after the agent's arguments
    were checked against the schema — an agent cannot pass them, the schema closes them:
    - `action_key`: the action and the call, hashed — the same on every retry, another for another call;
    - `work_item_key`: the work item the action belongs to, when it has one."""
    import hashlib
    import json

    from .services.connectors import declared_operation

    declared = declared_operation(instance, operation)
    if declared is None or not declared.platform_arguments:
        return {}
    # The platform's, never the agent's: what an agent wrote under these names is dropped first.
    for name in declared.platform_arguments:
        arguments.pop(name, None)
    filled: dict[str, Any] = {}
    if "action_key" in declared.platform_arguments:
        canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
        filled["action_key"] = hashlib.sha256(
            f"{ctx.action.id}:{operation}:{canonical}".encode()
        ).hexdigest()[:24]
    if "work_item_key" in declared.platform_arguments and ctx.action.work_item_id:
        from .db.models import WorkItem

        item = await ctx.session.get(WorkItem, ctx.action.work_item_id)
        if item is not None:
            filled["work_item_key"] = item.tracker_key
    return filled


async def _check(_ctx: EffectContext, params: dict[str, Any]) -> dict[str, Any]:
    """`check`: a condition, rendered with the results of the previous effects, that must be TRUE;
    otherwise the action fails, named by its `reason`, and what it had done is compensated. No third
    party system is touched: it is a control (a D+1 check, a closing), allowed by default.

    `verifier` (with `motif`) is the same effect under the name it had before S39-11: actions and
    workflows written with it keep running."""
    condition = str(params.get("condition", "")).strip().lower()
    reason = str(params.get("reason") or params.get("motif") or params.get("condition") or "")
    if condition not in {"true", "1", "yes", "oui"}:  # lang: fr-ok - `oui` kept for old workflows
        raise EffectRefused(f"check failed: {reason}")
    return {"ok": True, "reason": reason, "motif": reason}  # lang: fr-ok - `motif` kept one release


def reset() -> None:
    """For tests: only the core's effects remain."""
    _EFFECTS.clear()
    _POLICIES.clear()
    _EFFECTS["connector.call"] = _connector_call
    _EFFECTS["check"] = _check
    _POLICIES["check"] = "allowed"
    _EFFECTS["verifier"] = _check  # lang: fr-ok - alias for actions written before S39-11
    _POLICIES["verifier"] = "allowed"  # lang: fr-ok - alias for actions written before S39-11


reset()


# ───────────────────────────── the former names (removed in 0.23.0) ─────────────────────────────

_FORMER = {
    "EffetRefuse": "EffectRefused",  # lang: fr-ok alias:external
    "ContexteEffet": "EffectContext",  # lang: fr-ok alias:external
    "Executer": "Execute",  # lang: fr-ok alias:external
    "declarer_un_effet": "declare_effect",  # lang: fr-ok alias:external
    "politique_de_l_effet": "effect_policy",  # lang: fr-ok alias:external
    "effet": "effect",  # lang: fr-ok alias:external
    "effets_declares": "declared_effects",  # lang: fr-ok alias:external
    "rendre": "render",  # lang: fr-ok alias:external
    "reinitialiser": "reset",  # lang: fr-ok alias:external
}
alias_names(__name__, _FORMER, removed_in=EXTERNAL_ALIAS_REMOVAL)
