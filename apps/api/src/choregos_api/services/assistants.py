# SPDX-License-Identifier: Apache-2.0
"""Assistant installations: install, configure, bind the needs, readiness (ADR 0133, S63-02).

The manifest is never copied into the installation: it is read again from the installed plugin
(`plugin_files.plugin_assistant`), so what an upgrade changes is seen at once. The installation holds
what the organisation decided: the config, the owner and audience groups, the channel, the policy
overrides and, per need, the connector it is bound to.

`status` is derived, then stored for the listings that read it in SQL: `suspended` while a person has
stopped the assistant (`stopped_at`, ADR 0142 §4 bis: read first, so that no derivation undoes a stop) or
while the plugin is not installed (uninstalling a plugin suspends its assistants, installing it again
resumes them), `incomplete` while the readiness has an item, `ready` otherwise.

Rights are decided by the permissions `assistant:manage`, `assistant:use` and `assistant:audit`
(`assistant_access.holds`, S63-15) and nowhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import (
    AssistantInstallation,
    AssistantToolBinding,
    ConnectorOperation,
    OrgConnector,
)
from ..errors import conflict, forbidden, not_found, unprocessable
from ..rbac import Permission, Principal
from ..schemas.assistants import (
    Assistant,
    AssistantBind,
    AssistantBinding,
    AssistantInstall,
    AssistantLaunchBind,
    AssistantReadiness,
    AssistantReadinessItem,
    AssistantStop,
    AssistantUpdate,
)
from . import assistant_embed, assistant_launches, offered_knowledge, plugin_files
from .assistant_access import holds
from .connector_identity import supports_identity

INCOMPLETE = "incomplete"
READY = "ready"
SUSPENDED = "suspended"

# -- rights ----------------------------------------------------------------------------------------------


def require_admin(principal: Principal, org: str) -> None:
    if not principal.can(Permission.ASSISTANT_MANAGE, org):
        raise forbidden(
            f"assistants are installed, uninstalled and given owners by the administrator of {org}"
        )


def require_manager(principal: Principal, org: str, row: AssistantInstallation) -> None:
    if not holds(principal, Permission.ASSISTANT_MANAGE, org, row):
        raise forbidden(f"`{row.slug}` is managed by the administrator of {org} and by its owner groups")


# -- rows ------------------------------------------------------------------------------------------------


async def installation(session: AsyncSession, org_id: str, slug: str) -> AssistantInstallation:
    found = (
        await session.execute(
            select(AssistantInstallation).where(
                AssistantInstallation.org_id == org_id, AssistantInstallation.slug == slug
            )
        )
    ).scalar_one_or_none()
    if found is None:
        raise not_found("Assistant", slug)
    return found


async def installations(session: AsyncSession, org_id: str) -> list[AssistantInstallation]:
    found = await session.execute(
        select(AssistantInstallation)
        .where(AssistantInstallation.org_id == org_id)
        .order_by(AssistantInstallation.slug)
    )
    return list(found.scalars())


async def _bindings(
    session: AsyncSession, row: AssistantInstallation
) -> list[tuple[AssistantToolBinding, OrgConnector]]:
    found = await session.execute(
        select(AssistantToolBinding, OrgConnector)
        .join(OrgConnector, OrgConnector.id == AssistantToolBinding.connector_id)
        .where(AssistantToolBinding.installation_id == row.id)
        .order_by(AssistantToolBinding.need)
    )
    return [(b, c) for b, c in found.all()]


# -- binding checks --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Problem:
    kind: str
    message: str


async def _served(session: AsyncSession, connector: OrgConnector) -> dict[str, str]:
    """The operations the connector serves, as discovered, with their `access`."""
    found = await session.execute(
        select(ConnectorOperation.name, ConnectorOperation.access).where(
            ConnectorOperation.connector_id == connector.id
        )
    )
    return dict(found.all())


async def binding_problems(
    session: AsyncSession, connector: OrgConnector, tool: dict[str, Any]
) -> list[Problem]:
    """Why `connector` cannot satisfy the need `tool` declares: an operation it does not serve, an access
    that differs, an identity mode it cannot carry. Empty: it can."""
    served = await _served(session, connector)
    need = str(tool["need"])
    problems: list[Problem] = []
    for operation in tool["operations"]:
        if operation not in served:
            problems.append(
                Problem("need_not_served", f"`{connector.name}` does not serve `{operation}` (need `{need}`)")
            )
        elif served[operation] != tool["access"]:
            problems.append(
                Problem(
                    "need_not_served",
                    f"`{connector.name}` serves `{operation}` as `{served[operation]}`, "
                    f"need `{need}` is `{tool['access']}`",
                )
            )
    if not supports_identity(connector, str(tool["identity"])):
        problems.append(
            Problem(
                "identity_unsupported",
                f"`{connector.name}` does not support the identity `{tool['identity']}` (need `{need}`)",
            )
        )
    return problems


# -- the manifest and the readiness ----------------------------------------------------------------------


async def manifest(session: AsyncSession, row: AssistantInstallation) -> dict[str, Any] | None:
    found = await plugin_files.plugin_assistant(session, row.org_id, row.plugin, row.assistant_name)
    return found[1] if found is not None else None


def _tools(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in document.get("tools") or [] if isinstance(t, dict)]


def _missing_config(document: dict[str, Any], config: dict[str, Any]) -> list[str]:
    required = (document.get("config_schema") or {}).get("required") or []
    return [str(key) for key in required if config.get(key) in (None, "")]


async def readiness_items(session: AsyncSession, row: AssistantInstallation) -> list[AssistantReadinessItem]:
    """What is missing for `row` to be ready, and who may fix it (rule 9 of the specification)."""
    owners = list(row.owner_groups)
    document = await manifest(session, row)
    if document is None:
        return [
            AssistantReadinessItem(
                kind="assistant_missing",
                message=f"the plugin `{row.plugin}` no longer ships the assistant `{row.assistant_name}`",
                fix_by="org_admin",
                groups=[],
            )
        ]
    items = [
        AssistantReadinessItem(
            kind="config_missing",
            key=key,
            message=f"the configuration key `{key}` has no value",
            fix_by="owners",
            groups=owners,
        )
        for key in _missing_config(document, row.config)
    ]
    bound = {b.need: (b, c) for b, c in await _bindings(session, row)}
    for tool in _tools(document):
        need = str(tool["need"])
        if need not in bound:
            if tool.get("required", True):
                items.append(
                    AssistantReadinessItem(
                        kind="need_unbound",
                        need=need,
                        message=f"the required need `{need}` is not bound to a connector",
                        fix_by="owners",
                        groups=owners,
                    )
                )
            continue
        binding, connector = bound[need]
        if not binding.enabled:
            if tool.get("required", True):  # an optional need may be switched off, a required one may not
                misfit = await binding_problems(session, connector, tool)
                if misfit:  # switched off by an upgrade the connector no longer fits (S63-22)
                    items.extend(
                        AssistantReadinessItem(
                            kind=problem.kind,
                            need=need,
                            message=f"{problem.message}: bind it again once the connector fits",
                            fix_by="org_admin",
                            groups=[],
                        )
                        for problem in misfit
                    )
                else:
                    items.append(
                        AssistantReadinessItem(
                            kind="need_unbound",
                            need=need,
                            message=f"the required need `{need}` is switched off",
                            fix_by="owners",
                            groups=owners,
                        )
                    )
            continue
        for problem in await binding_problems(session, connector, tool):
            items.append(
                AssistantReadinessItem(
                    kind=problem.kind,
                    need=need,
                    message=problem.message,
                    # A connector's operations and identity modes are the connector's administrator's.
                    fix_by="org_admin",
                    groups=[],
                )
            )
    # ADR 0158: a required launch unbound, or a bound workflow that no longer serves it.
    items.extend(await assistant_launches.readiness_items(session, row, document, owners))
    return items


# -- an upgrade of the plugin changes the needs (S63-22) -------------------------------------------------


@dataclass(frozen=True, slots=True)
class NeedChange:
    """What an upgrade did to one need of an installation."""

    need: str
    kind: str  # removed | added | rebound | disabled | required_changed
    message: str


def _is_required(tool: dict[str, Any]) -> bool:
    return bool(tool.get("required", True))


def _required_change(need: str, tool: dict[str, Any]) -> NeedChange:
    now = "required" if _is_required(tool) else "optional"
    return NeedChange(need, "required_changed", f"`{need}` is now {now}")


async def upgrade_bindings(
    session: AsyncSession, row: AssistantInstallation, previous: dict[str, Any] | None
) -> list[NeedChange]:
    """Brings the bindings of `row` in line with the manifest its plugin now ships (`previous`: the one it
    had). A need the manifest dropped has its binding disabled and kept (the audit trail); a binding whose
    need changed is checked again against the connector (operations served, same access, identity
    supported): it follows the manifest when it fits and is disabled, with a readiness item, when it does
    not; a need added stays unbound, listed in the readiness; a `required` flag that moved is reported, the
    readiness being computed from the manifest."""
    document = await manifest(session, row)
    if document is None:
        return []
    tools = {str(t["need"]): t for t in _tools(document)}
    before = {str(t["need"]): t for t in _tools(previous or {})}
    changes: list[NeedChange] = []
    bound = await _bindings(session, row)
    for binding, connector in bound:
        tool = tools.get(binding.need)
        if tool is None:
            if binding.enabled:
                binding.enabled = False
                changes.append(
                    NeedChange(
                        binding.need, "removed", f"`{binding.need}` is no longer a need: its binding is off"
                    )
                )
            continue
        if binding.enabled:
            problems = await binding_problems(session, connector, tool)
            if problems:
                binding.enabled = False
                changes.append(
                    NeedChange(
                        binding.need,
                        "disabled",
                        f"`{binding.need}` changed, `{connector.name}` no longer fits: its binding is off: "
                        + "; ".join(p.message for p in problems),
                    )
                )
            else:
                operations, identity = [str(o) for o in tool["operations"]], str(tool["identity"])
                try:
                    approval = _approval_of_binding(tool, binding.approval, binding.need)
                except Exception:  # the owners' choice is below the new floor, or the need became a read
                    approval = str(tool["approval"]) if tool.get("approval") is not None else None
                if (operations, identity, approval) != (
                    list(binding.operations),
                    binding.identity,
                    binding.approval,
                ):
                    binding.operations, binding.identity, binding.approval = operations, identity, approval
                    changes.append(
                        NeedChange(
                            binding.need,
                            "rebound",
                            f"`{binding.need}` changed: `{connector.name}` still fits it",
                        )
                    )
        old = before.get(binding.need)
        if old is not None and _is_required(old) != _is_required(tool):
            changes.append(_required_change(binding.need, tool))
    bound_needs = {b.need for b, _ in bound}
    for need, tool in tools.items():
        if need in bound_needs:
            continue
        if need not in before:
            kind = "required" if _is_required(tool) else "optional"
            changes.append(NeedChange(need, "added", f"`{need}` is a new {kind} need, not bound yet"))
        elif _is_required(before[need]) != _is_required(tool):
            changes.append(_required_change(need, tool))
    # ADR 0158: the launches follow the same path.
    changes.extend(
        NeedChange(*change) for change in await assistant_launches.upgrade(session, row, document, previous)
    )
    await session.flush()
    return changes


async def upgrade_plugin_bindings(
    session: AsyncSession,
    org_id: str,
    plugin: str,
    previous: dict[str, dict[str, Any] | None],
    principal: Principal,
) -> dict[str, list[NeedChange]]:
    """`upgrade_bindings` for every installation of an assistant of `plugin` (`previous`: its old manifest by
    installation slug, read before the upgrade); audited, and the owners told through the installation's
    email notifier when a need changed. Returns what changed, by installation."""
    done: dict[str, list[NeedChange]] = {}
    for row in await installations(session, org_id):
        if row.plugin != plugin:
            continue
        changes = await upgrade_bindings(session, row, previous.get(row.slug))
        _, items = await refresh(session, row)
        if not changes:
            continue
        done[row.slug] = changes
        await record(session, principal, "assistant.upgrade", org_id=org_id, target_type="assistant",
                     target_id=row.slug, version=row.plugin_version,
                     changes=[{"need": c.need, "kind": c.kind} for c in changes],
                     readiness=[i.kind for i in items])  # fmt: skip
        lines = [f"- {c.message}" for c in changes] + [f"- to do: {i.message}" for i in items]
        await offered_knowledge.tell_owners(
            session,
            row,
            f"{row.slug}: version {row.plugin_version} changed what it needs",
            f"The upgrade of the plugin to {row.plugin_version} changed the needs of `{row.slug}`:\n"
            + "\n".join(lines),
            {"installation": row.slug, "version": row.plugin_version},
        )
    return done


async def refresh(
    session: AsyncSession, row: AssistantInstallation
) -> tuple[str, list[AssistantReadinessItem]]:
    """The live status and readiness; the row follows (and the plugin's version with it). A stopped
    installation reads `suspended` whatever the readiness says: only a resume clears a stop."""
    plugin_row = await plugin_files.installed_row(session, row.org_id, row.plugin)
    items = await readiness_items(session, row)
    if plugin_row is None:
        status = SUSPENDED
    else:
        status = READY if not items else INCOMPLETE
        row.plugin_version, row.manifest_digest = plugin_row.version, plugin_row.digest
    if row.stopped_at is not None:
        status = SUSPENDED
    if row.status != status:
        row.status = status
    await session.flush()
    return status, items


async def readiness(session: AsyncSession, row: AssistantInstallation) -> AssistantReadiness:
    status, items = await refresh(session, row)
    return AssistantReadiness(status=status, items=items)


def stopped_of(row: AssistantInstallation) -> AssistantStop | None:
    if row.stopped_at is None:
        return None
    return AssistantStop(at=row.stopped_at, by=row.stopped_by or "", reason=row.stopped_reason or "")


async def to_dto(session: AsyncSession, row: AssistantInstallation) -> Assistant:
    await refresh(session, row)
    document = await manifest(session, row)
    display = ((document or {}).get("metadata") or {}).get("display_name")
    bindings = [
        AssistantBinding(
            need=b.need,
            connector=c.name,
            operations=list(b.operations),
            identity=b.identity,
            approval=b.approval,
            enabled=b.enabled,
        )
        for b, c in await _bindings(session, row)
    ]
    return Assistant(
        id=row.id,
        slug=row.slug,
        plugin=row.plugin,
        plugin_version=row.plugin_version,
        assistant_name=row.assistant_name,
        display_name=display,
        config=dict(row.config),
        owner_groups=list(row.owner_groups),
        audience_groups=list(row.audience_groups),
        channel=row.channel,
        status=row.status,
        stopped=stopped_of(row),
        policy_overrides=dict(row.policy_overrides),
        bindings=bindings,
        launches=await assistant_launches.to_dtos(session, row, document),
        created_by=row.created_by,
        created_at=row.created_at,
    )


# -- gestures --------------------------------------------------------------------------------------------


def _check_config(document: dict[str, Any], config: dict[str, Any]) -> None:
    """A value the schema refuses is a 422; a required key left out is a readiness item, not a refusal."""
    assistant_embed.check_embed_origins(config)
    # `embed_origins` belongs to the platform (S63-12), not to the manifest's schema: a schema closed with
    # `additionalProperties: false` must not refuse it.
    own = {k: v for k, v in config.items() if k != assistant_embed.KEY}
    errors = [
        {"loc": ["config", *map(str, error.absolute_path)], "msg": error.message}
        for error in Draft202012Validator(document.get("config_schema") or {}).iter_errors(own)
        if error.validator != "required"
    ]
    if errors:
        raise unprocessable("the configuration does not match the assistant's `config_schema`", errors)


async def install(
    session: AsyncSession, org_id: str, org: str, body: AssistantInstall, principal: Principal
) -> AssistantInstallation:
    found = await plugin_files.plugin_assistant(session, org_id, body.plugin, body.assistant)
    if found is None:
        raise not_found("Assistant", f"{body.plugin}/{body.assistant}")
    plugin_row, document = found
    slug = body.slug or body.assistant
    existing = (
        await session.execute(
            select(AssistantInstallation.id).where(
                AssistantInstallation.org_id == org_id, AssistantInstallation.slug == slug
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise conflict(f"an assistant `{slug}` is already installed in {org}")
    _check_config(document, body.config)
    row = AssistantInstallation(
        org_id=org_id,
        slug=slug,
        plugin=plugin_row.name,
        plugin_version=plugin_row.version,
        manifest_digest=plugin_row.digest,
        assistant_name=body.assistant,
        config=dict(body.config),
        owner_groups=list(dict.fromkeys(body.owner_groups)),
        audience_groups=list(dict.fromkeys(body.audience_groups)),
        channel=body.channel,
        status=INCOMPLETE,
        policy_overrides={},
        created_by=principal.email,
    )
    session.add(row)
    await session.flush()
    await offered_knowledge.copy_seeds(
        session, row, document, plugin_row, await plugin_files.plugin_root(session, plugin_row), principal
    )
    await refresh(session, row)
    await record(session, principal, "assistant.install", org_id=org_id, target_type="assistant",
                 target_id=slug, plugin=plugin_row.name, version=plugin_row.version)  # fmt: skip
    return row


async def update(
    session: AsyncSession,
    org_id: str,
    org: str,
    row: AssistantInstallation,
    body: AssistantUpdate,
    principal: Principal,
) -> AssistantInstallation:
    require_manager(principal, org, row)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    owners = changes.pop("owner_groups", None)
    if owners is not None and list(dict.fromkeys(owners)) != list(row.owner_groups):
        require_admin(principal, org)
        row.owner_groups = list(dict.fromkeys(owners))
    if "config" in changes:
        document = await manifest(session, row)
        if document is not None:
            _check_config(document, changes["config"])
        row.config = dict(changes["config"])
    if "audience_groups" in changes:
        row.audience_groups = list(dict.fromkeys(changes["audience_groups"]))
    if "channel" in changes:
        row.channel = changes["channel"]
    if "policy_overrides" in changes:
        row.policy_overrides = dict(changes["policy_overrides"])
    await session.flush()
    await refresh(session, row)
    await record(session, principal, "assistant.update", org_id=org_id, target_type="assistant",
                 target_id=row.slug, fields=sorted(body.model_dump(exclude_unset=True)))  # fmt: skip
    return row


async def uninstall(
    session: AsyncSession, org_id: str, row: AssistantInstallation, principal: Principal
) -> None:
    for binding, _ in await _bindings(session, row):
        await session.delete(binding)
    await session.flush()
    from ..secret_store import get_secret_store
    from .task_delivery import teams_secret_name

    await get_secret_store().delete(session, org_id, teams_secret_name(row.slug))  # its sealed Teams URL
    await session.delete(row)
    await session.flush()
    await record(session, principal, "assistant.uninstall", org_id=org_id, target_type="assistant",
                 target_id=row.slug)  # fmt: skip


#: The order of approvals: an owner moves a write up this scale, never down (S63-10).
_APPROVAL_RANK = {"none": 0, "inline": 1, "console": 2}


def _approval_of_binding(tool: dict[str, Any], asked: str | None, need: str) -> str | None:
    """The approval a binding takes: the manifest's (the floor) unless the owners raise it to `console`."""
    floor = tool.get("approval")
    if asked is None:
        return str(floor) if floor is not None else None
    if floor is None:
        raise unprocessable(f"`{need}` is a read: it has no approval to choose")
    if _APPROVAL_RANK[asked] < _APPROVAL_RANK[str(floor)]:
        raise unprocessable(
            f"`{need}` needs at least the `{floor}` approval its plugin declares: `{asked}` is below it"
        )
    return asked


async def bind(
    session: AsyncSession,
    org_id: str,
    org: str,
    row: AssistantInstallation,
    need: str,
    body: AssistantBind,
    principal: Principal,
) -> AssistantInstallation:
    require_manager(principal, org, row)
    document = await manifest(session, row)
    if document is None:
        raise unprocessable(f"the plugin `{row.plugin}` no longer ships the assistant `{row.assistant_name}`")
    tool = next((t for t in _tools(document) if t["need"] == need), None)
    if tool is None:
        raise not_found("Need", need)
    connector = (
        await session.execute(
            select(OrgConnector).where(OrgConnector.org_id == org_id, OrgConnector.name == body.connector)
        )
    ).scalar_one_or_none()
    if connector is None:
        raise unprocessable(f"`{body.connector}` is not a connector of {org}")
    approval = _approval_of_binding(tool, body.approval, need)
    if not body.enabled and tool.get("required", True):
        raise unprocessable(f"`{need}` is a required need: it cannot be switched off")
    if body.enabled:
        problems = await binding_problems(session, connector, tool)
        if problems:
            raise unprocessable(
                f"`{body.connector}` cannot satisfy the need `{need}`",
                [{"loc": ["need", need], "msg": p.message} for p in problems],
            )
    binding = (
        await session.execute(
            select(AssistantToolBinding).where(
                AssistantToolBinding.installation_id == row.id, AssistantToolBinding.need == need
            )
        )
    ).scalar_one_or_none()
    if binding is None:
        binding = AssistantToolBinding(org_id=org_id, installation_id=row.id, need=need)
        session.add(binding)
    binding.connector_id = connector.id
    binding.operations = [str(o) for o in tool["operations"]]
    binding.identity = str(tool["identity"])
    binding.approval = approval
    binding.enabled = body.enabled
    await session.flush()
    await refresh(session, row)
    await record(session, principal, "assistant.bind", org_id=org_id, target_type="assistant",
                 target_id=row.slug, need=need, connector=connector.name, enabled=body.enabled)  # fmt: skip
    return row


async def bind_launch(
    session: AsyncSession,
    org_id: str,
    org: str,
    row: AssistantInstallation,
    launch: str,
    body: AssistantLaunchBind,
    principal: Principal,
) -> AssistantInstallation:
    """Binds a launch to a project's workflow: `assistant:manage` here, `item:control` in its project."""
    require_manager(principal, org, row)
    document = await manifest(session, row)
    if document is None:
        raise unprocessable(f"the plugin `{row.plugin}` no longer ships the assistant `{row.assistant_name}`")
    await assistant_launches.bind(session, org_id, org, row, document, launch, body, principal)
    await refresh(session, row)
    return row
