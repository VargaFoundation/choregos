# SPDX-License-Identifier: Apache-2.0
"""Assistant installations: install, configure, bind the needs, readiness (ADR 0133, S63-02).

The manifest is never copied into the installation: it is read again from the installed plugin
(`plugin_installer.plugin_assistant`), so what an upgrade changes is seen at once. The installation holds
what the organisation decided: the config, the owner and audience groups, the channel, the policy
overrides and, per need, the connector it is bound to.

`status` is derived, then stored for the listings that read it in SQL: `suspended` while the plugin is not
installed (uninstalling a plugin suspends its assistants, installing it again resumes them), `incomplete`
while the readiness has an item, `ready` otherwise.

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
    AssistantReadiness,
    AssistantReadinessItem,
    AssistantUpdate,
)
from . import plugin_installer
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
    found = await plugin_installer.plugin_assistant(session, row.org_id, row.plugin, row.assistant_name)
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
    return items


async def refresh(
    session: AsyncSession, row: AssistantInstallation
) -> tuple[str, list[AssistantReadinessItem]]:
    """The live status and readiness; the row follows (and the plugin's version with it)."""
    plugin_row = await plugin_installer.installed_row(session, row.org_id, row.plugin)
    items = await readiness_items(session, row)
    if plugin_row is None:
        status = SUSPENDED
    else:
        status = READY if not items else INCOMPLETE
        row.plugin_version, row.manifest_digest = plugin_row.version, plugin_row.digest
    if row.status != status:
        row.status = status
    await session.flush()
    return status, items


async def readiness(session: AsyncSession, row: AssistantInstallation) -> AssistantReadiness:
    status, items = await refresh(session, row)
    return AssistantReadiness(status=status, items=items)


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
        policy_overrides=dict(row.policy_overrides),
        bindings=bindings,
        created_by=row.created_by,
        created_at=row.created_at,
    )


# -- gestures --------------------------------------------------------------------------------------------


def _check_config(document: dict[str, Any], config: dict[str, Any]) -> None:
    """A value the schema refuses is a 422; a required key left out is a readiness item, not a refusal."""
    errors = [
        {"loc": ["config", *map(str, error.absolute_path)], "msg": error.message}
        for error in Draft202012Validator(document.get("config_schema") or {}).iter_errors(config)
        if error.validator != "required"
    ]
    if errors:
        raise unprocessable("the configuration does not match the assistant's `config_schema`", errors)


async def install(
    session: AsyncSession, org_id: str, org: str, body: AssistantInstall, principal: Principal
) -> AssistantInstallation:
    found = await plugin_installer.plugin_assistant(session, org_id, body.plugin, body.assistant)
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
    await session.delete(row)
    await session.flush()
    await record(session, principal, "assistant.uninstall", org_id=org_id, target_type="assistant",
                 target_id=row.slug)  # fmt: skip


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
    binding.approval = tool.get("approval")
    binding.enabled = body.enabled
    await session.flush()
    await refresh(session, row)
    await record(session, principal, "assistant.bind", org_id=org_id, target_type="assistant",
                 target_id=row.slug, need=need, connector=connector.name, enabled=body.enabled)  # fmt: skip
    return row
