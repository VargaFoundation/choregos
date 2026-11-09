# SPDX-License-Identifier: Apache-2.0
"""The organisation's connectors, and the policy of each operation (ADR 0034).

A directory, a fleet manager, an MCP server: the organisation's administrator declares them
once, named, and decides for each operation whether it is allowed, subject to approval
(a governed action, ADR 0035) or forbidden, and for which groups of projects.
A project can only TIGHTEN: an `approval` operation never becomes `allowed` below the
organisation's level, and only the administrator changes the groups (the second lock of ADR 0014).
"""

from __future__ import annotations

from typing import Annotated, Any

from choregos_core.secrets import is_store_reference
from fastapi import APIRouter, Path, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..config import get_settings
from ..db.models import (
    ConnectorOperation,
    Organization,
    OrgConnector,
    OrgConnectorProject,
    ProjectOperationPolicy,
)
from ..deps import Db, Me, ProjectCtx, is_instance_admin
from ..errors import conflict, forbidden, not_found, unprocessable, upstream
from ..rbac import Permission
from ..schemas import (
    ConnectorDiscovery,
    OperationDto,
    OperationPatch,
    OrgConnectorCreate,
    OrgConnectorDto,
    OrgConnectorUpdate,
    ProjectOperationDto,
    ProjectOperationPut,
    ProjectOrgConnectorDto,
    SecretValue,
)
from ..secret_store import secret_states
from ..services import plugin_servers
from ..services.argument_grants import refuse_constraint
from ..services.catalogue_rights import (
    SELECTED_PROJECTS,
    CatalogueAction,
    connector_enabled_for,
    decide,
    enabled_ids,
    enabled_projects_of,
    visible_projects,
)
from ..services.connector_secrets import (
    SECRET_FIELD,
    drop_replaced_store_values,
    put_values,
    refs_with_values,
    require_store,
)
from ..services.connectors import (
    RANK,
    check_type_config,
    declared_operation,
    spec_to_declare,
    stricter,
    type_spec,
    verify_secrets,
)
from ..services.egress import egress_gate_for_connector, egress_scope
from ..services.mcp_gateway import check_declaration, connector_client, route_of
from ..services.mcp_gateway_identity import CallIdentity, GatewayRefusal, identity_of_person
from ..services.org_connector_access import require_connector_admin
from ..services.organisations import organisation_by_slug
from ..services.platform_network import (
    PlatformNetworkRefused,
    check_connector_declaration,
    enforce_connector_call,
    refusal_error,
)

router = APIRouter(tags=["connectors"])

Name = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]


async def _instance(session: AsyncSession, organisation: Organization, name: str) -> OrgConnector:
    instance = (
        await session.execute(
            select(OrgConnector).where(OrgConnector.org_id == organisation.id, OrgConnector.name == name)
        )
    ).scalar_one_or_none()
    if instance is None:
        raise not_found("Connector", name)
    return instance


async def _dto(session: AsyncSession, instance: OrgConnector, principal: Me) -> OrgConnectorDto:
    operations = (
        await session.execute(
            select(ConnectorOperation)
            .where(ConnectorOperation.connector_id == instance.id)
            .order_by(ConnectorOperation.name)
        )
    ).scalars()
    dto = OrgConnectorDto.model_validate(instance)
    dto.operations = [OperationDto.model_validate(o) for o in operations]
    dto.secrets = await secret_states(session, instance.org_id, instance.secret_refs or {})
    dto.server = plugin_servers.console_view(instance)
    organisation = await session.get(Organization, instance.org_id)
    if organisation is not None and instance.availability == SELECTED_PROJECTS:
        visible = await visible_projects(session, principal, organisation.id, organisation.slug)
        dto.enabled_projects = (await enabled_projects_of(session, [instance], visible, organisation.id)).get(
            instance.id, []
        )
    return dto


async def _audit_secrets(
    session: AsyncSession,
    principal: Me,
    organisation: Organization,
    instance: OrgConnector,
    action: str,
    fields_versions: dict[str, int | None],
) -> None:
    """Who set or cleared which field, at which version — never the value."""
    for field, version in fields_versions.items():
        await record(
            session,
            principal,
            action,
            org_id=organisation.id,
            target_type="org_connector",
            target_id=instance.id,
            connector=instance.name,
            field=field,
            version=version,
        )


@router.get("/orgs/{org}/connectors", response_model=list[OrgConnectorDto], operation_id="listOrgConnectors")
async def list_org_connectors(org: str, session: Db, principal: Me) -> list[OrgConnectorDto]:
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden(f"the connectors of {org} are readable by its members only")
    organisation = await organisation_by_slug(session, org)
    instances = (
        await session.execute(
            select(OrgConnector).where(OrgConnector.org_id == organisation.id).order_by(OrgConnector.name)
        )
    ).scalars()
    return [await _dto(session, instance, principal) for instance in instances]


@router.post(
    "/orgs/{org}/connectors",
    response_model=OrgConnectorDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createOrgConnector",
)
async def create_org_connector(
    org: str, body: OrgConnectorCreate, session: Db, principal: Me
) -> OrgConnectorDto:
    """An instance is born with the operations its type declares, at their default policy:
    a read is allowed, a write is subject to approval."""
    from choregos_adapters import connector_types

    require_connector_admin(principal, org)
    organisation = await organisation_by_slug(session, org)
    kind = body.kind
    if kind is None:
        kinds = sorted({k for k, t, _ in connector_types() if t == body.type})
        if len(kinds) != 1:
            raise unprocessable(f"`{body.type}`: specify the capability (`kind`) among {kinds or 'none'}")
        kind = kinds[0]
    if kind == "gateway":
        await _check_org_gateway(session, organisation, body.type)
    return await declare_org_connector(
        session, principal, organisation, body.model_copy(update={"kind": kind})
    )


async def _check_org_gateway(session: AsyncSession, organisation: Organization, type_name: str) -> None:
    """An organisation brings at most ONE model gateway (S44-04), never the `direct` one in staging or prod.

    Its catalogue and its runs read the organisation's gateway (ADR 0060): two would leave the choice to
    the alphabetical order of their names. The `direct` gateway hands out the key it was given: no key
    per run, no hard cap, no measured cost — refused here as it is for a project (`putConnector`).
    """
    if type_name == "direct" and get_settings().env in {"staging", "prod"}:
        raise unprocessable(
            "the `direct` gateway is refused in this environment: it measures no cost and "
            "enforces no cap. Use `litellm` or `openai-compatible`."
        )
    existing = (
        await session.execute(
            select(OrgConnector.name).where(
                OrgConnector.org_id == organisation.id, OrgConnector.kind == "gateway"
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise conflict(
            f"{organisation.slug} already brings its model gateway `{existing}`: "
            "remove it before declaring another"
        )


async def declare_org_connector(
    session: AsyncSession,
    principal: Me,
    organisation: Organization,
    body: OrgConnectorCreate,
    *,
    catalogue_entry: str | None = None,
) -> OrgConnectorDto:
    """Declares a connector of the organisation with the operations its type declares.

    The one door: a connector written by hand and one installed from the MCP catalogue (ADR 0065)
    both pass through the same checks (secrets by reference only, a free name) and leave the same
    audit row; the latter also records which catalogue entry it came from. `body.kind` is resolved."""
    assert body.kind is not None
    name, kind, type_name = body.name, body.kind, body.type
    config, secret_refs = body.config, body.secret_refs
    spec = spec_to_declare(kind, type_name)
    check_type_config(kind, type_name, config)
    await check_declaration(session, organisation, kind, type_name)  # ADR 0105: mode `required`, no gateway
    prefix = f"connector/{name}"
    verify_secrets(
        spec,
        type_name,
        config,
        secret_refs,
        may_reference=not secret_refs or await is_instance_admin(session, principal),
        secret_values=body.secret_values,
        store_prefix=prefix,
    )
    store = require_store(body.secret_values)
    await check_connector_declaration(session, organisation.id, body.config, body.name)
    await egress_gate_for_connector(
        session,
        principal,
        organisation,
        kind,
        body.type,
        body.config,
        body.name,
        body.allow_egress,
        origin=f"mcp-catalogue:{catalogue_entry}" if catalogue_entry else None,
    )
    already = (
        await session.execute(
            select(OrgConnector.id).where(OrgConnector.org_id == organisation.id, OrgConnector.name == name)
        )
    ).first()
    if already is not None:
        raise conflict(f"a connector `{name}` already exists in {organisation.slug}")
    instance = OrgConnector(
        org_id=organisation.id,
        name=name,
        kind=kind,
        type=type_name,
        config=config,
        secret_refs=refs_with_values(secret_refs, prefix, body.secret_values) or None,
        created_by=principal.email,
        catalogue_entry=catalogue_entry,
        availability=body.availability,
    )
    session.add(instance)
    await session.flush()
    metas = await put_values(session, store, organisation.id, prefix, body.secret_values, principal.email)
    for operation in spec.operations:
        session.add(
            ConnectorOperation(
                org_id=organisation.id,
                connector_id=instance.id,
                name=operation.name,
                access=operation.access,
                policy=operation.default_policy,
                groups=[],
                input_schema=operation.input_schema,
                description=operation.description or None,
            )
        )
    await session.flush()
    audit: dict[str, Any] = {"name": name, "type": type_name}
    if catalogue_entry:
        audit["catalogue"] = catalogue_entry
    await record(
        session,
        principal,
        "org.connector.create",
        org_id=organisation.id,
        target_type="org_connector",
        target_id=instance.id,
        **audit,
    )
    await _audit_secrets(
        session,
        principal,
        organisation,
        instance,
        "connector.secret.set",
        {f: m.version for f, m in metas.items()},
    )
    return await _dto(session, instance, principal)


@router.get("/orgs/{org}/connectors/{name}", response_model=OrgConnectorDto, operation_id="getOrgConnector")
async def get_org_connector(org: str, name: Name, session: Db, principal: Me) -> OrgConnectorDto:
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden(f"the connectors of {org} are readable by its members only")
    return await _dto(
        session, await _instance(session, await organisation_by_slug(session, org), name), principal
    )


@router.patch(
    "/orgs/{org}/connectors/{name}", response_model=OrgConnectorDto, operation_id="updateOrgConnector"
)
async def update_org_connector(
    org: str, name: Name, body: OrgConnectorUpdate, session: Db, principal: Me
) -> OrgConnectorDto:
    """Edit what an administrator declared: the configuration, and the secrets. `secret_refs` and
    `secret_values` merge into the connector's; a field given as a reference after being a stored
    value drops the stored value. The operations are not touched (their own route)."""
    require_connector_admin(principal, org)
    organisation = await organisation_by_slug(session, org)
    instance = await _instance(session, organisation, name)
    server = plugin_servers.spec_of_connector(instance)
    spec = server or type_spec(instance.kind, instance.type)
    prefix = f"connector/{name}"
    config = body.config if body.config is not None else dict(instance.config or {})
    if body.config is not None and server is None:
        check_type_config(instance.kind, instance.type, config)
    elif body.config is not None:
        # A plugin's server (ADR 0096): its own schema, and the hosts it would reach within the bounds;
        # never bound to its eval instance by an edit (#1372).
        plugin_servers.refuse_fake_binding(config)
        problems = plugin_servers.configuration_problems(instance.plugin_server or {}, config, partial=True)
        if problems:
            raise unprocessable(
                f"the configuration of the server `{name}` breaks its schema",
                [{"loc": ["body", "config"], "msg": p} for p in problems],
            )
        await plugin_servers.check_egress(session, instance, config)
    refs_in, values = dict(body.secret_refs or {}), dict(body.secret_values or {})
    # The connector keeps the references the body does not replace (a PATCH merges): moving one that
    # reads the deployment to another host is the operator's too, or the key follows the new URL.
    operator_refs = [
        r for f, r in (instance.secret_refs or {}).items() if f not in refs_in and f not in values
    ]
    deployment_held = any(not is_store_reference(r) for r in [*refs_in.values(), *operator_refs])
    may_reference = not deployment_held or await is_instance_admin(session, principal)
    if body.config is not None and not may_reference:
        raise unprocessable(
            "this connector reads a key the deployment holds (`env:`): only a platform administrator "
            "changes its configuration; type the organisation's own value to edit it yourself"
        )
    verify_secrets(
        spec,
        instance.type,
        config,
        refs_in,
        may_reference=may_reference,
        secret_values=values,
        store_prefix=prefix,
    )
    store = require_store(values)
    await check_connector_declaration(session, organisation.id, config, name)
    if body.config is not None and server is None:
        await egress_gate_for_connector(
            session, principal, organisation, instance.kind, instance.type, config, name, body.allow_egress
        )
    before = dict(instance.secret_refs or {})
    after = refs_with_values({**before, **refs_in}, prefix, values)
    metas = await put_values(session, store, organisation.id, prefix, values, principal.email)
    cleared = await drop_replaced_store_values(session, store, organisation.id, before, after)
    instance.config = config
    instance.secret_refs = after or None
    instance.status = "unknown"
    if body.availability is not None and body.availability != instance.availability:
        decision = decide(principal, org, CatalogueAction.SET_AVAILABILITY)
        if not decision.allowed:  # pragma: no cover - `require_connector_admin` already refused
            raise forbidden(decision.reason or "refused")
        previous, instance.availability = instance.availability, body.availability
        await record(
            session,
            principal,
            "org.connector.availability",
            org_id=organisation.id,
            target_type="org_connector",
            target_id=instance.id,
            name=name,
            previous=previous,
            availability=body.availability,
        )
    await session.flush()
    await record(
        session,
        principal,
        "org.connector.update",
        org_id=organisation.id,
        target_type="org_connector",
        target_id=instance.id,
        name=name,
        config_changed=body.config is not None,
    )
    await _audit_secrets(
        session,
        principal,
        organisation,
        instance,
        "connector.secret.set",
        {f: m.version for f, m in metas.items()},
    )
    await _audit_secrets(
        session, principal, organisation, instance, "connector.secret.clear", dict.fromkeys(cleared)
    )
    # A plugin's server reads its configuration from its Secret: the change rolls its pods.
    await plugin_servers.reconcile(session, instance)
    return await _dto(session, instance, principal)


@router.put(
    "/orgs/{org}/connectors/{name}/secrets/{field}",
    response_model=OrgConnectorDto,
    operation_id="setOrgConnectorSecret",
)
async def set_org_connector_secret(
    org: str,
    name: Name,
    field: Annotated[str, Path(pattern=SECRET_FIELD)],
    body: SecretValue,
    session: Db,
    principal: Me,
) -> OrgConnectorDto:
    """Type a secret once (or replace it): sealed, never returned. The response says it is set."""
    require_connector_admin(principal, org)
    organisation = await organisation_by_slug(session, org)
    instance = await _instance(session, organisation, name)
    verify_secrets(
        plugin_servers.spec_of_connector(instance) or type_spec(instance.kind, instance.type),
        instance.type,
        {},
        {},
        secret_values={field: body.value},
        store_prefix=f"connector/{name}",
    )
    store = require_store({field: body.value})
    prefix = f"connector/{name}"
    before = dict(instance.secret_refs or {})
    metas = await put_values(session, store, organisation.id, prefix, {field: body.value}, principal.email)
    instance.secret_refs = refs_with_values(before, prefix, {field: body.value})
    instance.status = "unknown"
    await session.flush()
    await _audit_secrets(
        session, principal, organisation, instance, "connector.secret.set", {field: metas[field].version}
    )
    await plugin_servers.reconcile(session, instance)
    return await _dto(session, instance, principal)


@router.delete(
    "/orgs/{org}/connectors/{name}/secrets/{field}",
    response_model=OrgConnectorDto,
    operation_id="clearOrgConnectorSecret",
)
async def clear_org_connector_secret(
    org: str,
    name: Name,
    field: Annotated[str, Path(pattern=SECRET_FIELD)],
    session: Db,
    principal: Me,
) -> OrgConnectorDto:
    """Remove a secret field's value or reference: the connector no longer holds it."""
    require_connector_admin(principal, org)
    organisation = await organisation_by_slug(session, org)
    instance = await _instance(session, organisation, name)
    before = dict(instance.secret_refs or {})
    if field not in before:
        raise not_found("Secret", field)
    after = {f: r for f, r in before.items() if f != field}
    await drop_replaced_store_values(session, require_store(None), organisation.id, before, after)
    instance.secret_refs = after or None
    instance.status = "unknown"
    await session.flush()
    await _audit_secrets(session, principal, organisation, instance, "connector.secret.clear", {field: None})
    await plugin_servers.reconcile(session, instance)
    return await _dto(session, instance, principal)


@router.delete(
    "/orgs/{org}/connectors/{name}", status_code=status.HTTP_204_NO_CONTENT, operation_id="deleteOrgConnector"
)
async def delete_org_connector(org: str, name: Name, session: Db, principal: Me) -> Response:
    require_connector_admin(principal, org)
    organisation = await organisation_by_slug(session, org)
    instance = await _instance(session, organisation, name)
    if instance.plugin_server is not None:
        raise conflict(
            f"`{name}` is the server of the plugin `{instance.plugin_server.get('plugin')}`: uninstall the "
            "plugin to remove it"
        )
    # Its sealed secrets go with it: a value nothing points to is a value nobody can see or remove.
    await drop_replaced_store_values(session, require_store(None), organisation.id, instance.secret_refs, {})
    await session.delete(instance)
    await record(
        session,
        principal,
        "org.connector.delete",
        org_id=organisation.id,
        target_type="org_connector",
        target_id=instance.id,
        name=name,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/orgs/{org}/connectors/{name}/operations/{operation}",
    response_model=OperationDto,
    operation_id="updateConnectorOperation",
)
async def update_operation(
    org: str, name: Name, operation: str, body: OperationPatch, session: Db, principal: Me
) -> OperationDto:
    """An operation's policy, groups and price: the organisation's decision."""
    require_connector_admin(principal, org)
    organisation = await organisation_by_slug(session, org)
    instance = await _instance(session, organisation, name)
    op_row = (
        await session.execute(
            select(ConnectorOperation).where(
                ConnectorOperation.connector_id == instance.id, ConnectorOperation.name == operation
            )
        )
    ).scalar_one_or_none()
    if op_row is None:
        raise not_found("Operation", operation)
    if body.policy == "allowed":
        declared = declared_operation(instance, operation)
        if declared is not None and declared.human_approval:
            raise unprocessable(
                f"{name}/{operation} is approved by a person, always (its type declares it): "
                "`approval` or `forbidden` only"
            )
    if body.policy is not None:
        op_row.policy = body.policy
    if body.groups is not None:
        op_row.groups = sorted(set(body.groups))
    if "price_usd" in body.model_fields_set:
        op_row.price_usd = body.price_usd
    if "approver_group" in body.model_fields_set:
        if body.approver_group and op_row.access != "write":
            raise unprocessable(f"{name}/{operation} is a read: nobody approves it")
        op_row.approver_group = body.approver_group
    if "result_max_bytes" in body.model_fields_set:
        from ..config import get_settings

        ceiling = get_settings().broker_result_ceiling_bytes
        if body.result_max_bytes is not None and body.result_max_bytes > ceiling:
            raise unprocessable(
                f"{name}/{operation}: a result cap of {body.result_max_bytes} bytes is above the"
                f" platform's ceiling ({ceiling} bytes, CHOREGOS_BROKER_RESULT_CEILING_BYTES)"
            )
        op_row.result_max_bytes = body.result_max_bytes
    if "arguments" in body.model_fields_set:
        if body.arguments is not None:
            refuse_constraint(operation, op_row.input_schema, body.arguments, f"{name}.{operation}")
        op_row.arguments = body.arguments
    await record(
        session,
        principal,
        "org.connector.operation",
        org_id=organisation.id,
        target_type="connector_operation",
        target_id=op_row.id,
        connector=name,
        operation=operation,
        **body.model_dump(exclude_unset=True),
    )
    return OperationDto.model_validate(op_row)


async def _discovery_identity(
    organisation: Organization, principal: Me, instance: OrgConnector
) -> CallIdentity:
    return identity_of_person(organisation, principal.email, instance, "discovery")


@router.post(
    "/orgs/{org}/connectors/{name}/discover",
    response_model=ConnectorDiscovery,
    operation_id="discoverConnectorOperations",
)
async def discover_operations(org: str, name: Name, session: Db, principal: Me) -> ConnectorDiscovery:
    """Ask the server for its tools and derive the diff from them (ADR 0034).

    A NEW tool is born closed (`forbidden`): a server does not add a capability behind the
    administrator's back. A tool whose input schema DRIFTS is closed again: an `order_workstation`
    that suddenly accepts a quantity is no longer the operation that was opened. A tool the
    server no longer offers disappears, along with what the projects tightened on it."""
    import httpx
    from choregos_adapters.mcp import McpError, schema_digest
    from choregos_core import utcnow
    from choregos_core.redaction import redact
    from choregos_core.secrets import SecretNotFound

    require_connector_admin(principal, org)
    organisation = await organisation_by_slug(session, org)
    instance = await _instance(session, organisation, name)
    # ADR 0105: the route is judged before anything leaves; a refusal is a 409 and never marks the
    # connector `error` (the connector is fine, its route is not).
    try:
        route = await route_of(session, instance)
    except GatewayRefusal as refused:
        raise conflict(str(refused), extra={"code": refused.code}) from refused
    try:
        await enforce_connector_call(session, instance, polite=False, host=route.host)
    except PlatformNetworkRefused as refused:
        raise refusal_error(refused) from refused
    try:
        client: Any = await connector_client(
            session, instance, lambda: _discovery_identity(organisation, principal, instance)
        )
        list_tools_fn = getattr(client, "list_tools", None)
        if list_tools_fn is None:
            raise unprocessable(f"`{instance.type}` does not discover its operations: its type declares them")
        async with egress_scope(session, organisation, "api", f"org-connector:{name}"):
            tools = await list_tools_fn()
    except GatewayRefusal as refused:  # a lost assertion key: refused, the connector is not `error`
        raise conflict(str(refused), extra={"code": refused.code}) from refused
    except (McpError, SecretNotFound, httpx.HTTPError) as failure:
        instance.status, instance.last_check_at = "error", utcnow()
        instance.last_error = redact(str(failure))[:1000]
        # The response is an error, and the request would be rolled back with it: the connector's
        # state, however, must stay: the screen says why it is down. Nothing follows this commit.
        await session.commit()
        raise upstream(instance.name, redact(str(failure))[:500]) from failure
    existing_ops = {
        op.name: op
        for op in (
            await session.execute(
                select(ConnectorOperation).where(ConnectorOperation.connector_id == instance.id)
            )
        ).scalars()
    }
    diff = ConnectorDiscovery()
    declared = (instance.plugin_server or {}).get("operations")
    undeclared_tools: list[str] = []
    for tool in tools:
        input_digest = schema_digest(tool.input_schema)
        access_kind = "read" if tool.read_only else "write"
        if declared is not None:
            # A plugin's server (ADR 0096): its `operations` are the floor. A tool it does not declare
            # is never added; a declared one keeps its declared access, whatever the server hints.
            if tool.name not in declared:
                undeclared_tools.append(tool.name)
                continue
            access_kind = str(declared[tool.name]["access"])
        operation = existing_ops.pop(tool.name, None)
        if operation is None:
            session.add(
                ConnectorOperation(
                    org_id=organisation.id,
                    connector_id=instance.id,
                    name=tool.name,
                    access=access_kind,
                    policy="forbidden",
                    groups=[],
                    schema_digest=input_digest,
                    input_schema=tool.input_schema,
                    output_schema=getattr(tool, "output_schema", None),
                    description=tool.description or None,
                )
            )
            diff.added.append(tool.name)
            continue
        # The declared output schema follows the server on every discovery: it bounds what the agent
        # reads (#755), it does not change what the tool does, so it does not close the operation.
        operation.output_schema = getattr(tool, "output_schema", None)
        if declared is not None and operation.schema_digest is None:
            # A declared operation the server lists for the first time: its schema is learnt, not a
            # drift — the plugin's declaration opened it at its floor (ADR 0096).
            operation.schema_digest, operation.input_schema = input_digest, tool.input_schema
            operation.description = operation.description or tool.description or None
            diff.unchanged += 1
            continue
        if operation.schema_digest != input_digest or operation.access != access_kind:
            operation.policy, operation.schema_digest, operation.access = (
                "forbidden",
                input_digest,
                access_kind,
            )
            operation.input_schema = tool.input_schema
            operation.description = tool.description or operation.description
            diff.changed.append(tool.name)
        else:
            diff.unchanged += 1
    for tool_name, operation in existing_ops.items():
        if declared is not None and tool_name in declared:
            continue  # declared by the plugin: kept even while the server does not list it
        await session.delete(operation)
        diff.removed.append(tool_name)
    instance.status, instance.last_check_at, instance.last_error = "ok", utcnow(), None
    await session.flush()
    await record(
        session,
        principal,
        "org.connector.discover",
        org_id=organisation.id,
        target_type="org_connector",
        target_id=instance.id,
        **diff.model_dump(),
        **({"undeclared": undeclared_tools} if undeclared_tools else {}),
    )
    return diff


# ───────────────────────────── what a project sees of it, and tightens ─────────────────────────────


async def _project_operations(ctx: ProjectCtx, session: AsyncSession) -> list[ProjectOperationDto]:
    """The operations open to this project: those whose groups intersect its own (no group:
    every project), with the organisation's policy, its own, and the effective one."""
    project_groups = {str(g) for g in ((ctx.project.config or {}).get("groups") or [])}
    op_rows = (
        await session.execute(
            select(ConnectorOperation, OrgConnector.name)
            .join(OrgConnector, OrgConnector.id == ConnectorOperation.connector_id)
            .where(ConnectorOperation.org_id == ctx.project.org_id)
            .order_by(OrgConnector.name, ConnectorOperation.name)
        )
    ).all()
    rows = (
        (
            await session.execute(
                select(ProjectOperationPolicy).where(ProjectOperationPolicy.project_id == ctx.id)
            )
        )
        .scalars()
        .all()
    )
    tightened = {p.operation_id: p.policy for p in rows}
    narrowed_by = {p.operation_id: p.arguments for p in rows}
    allumes = await enabled_ids(session, ctx.project)
    closed_connectors = {
        row.id
        for row in (
            await session.execute(
                select(OrgConnector).where(
                    OrgConnector.org_id == ctx.project.org_id, OrgConnector.availability == SELECTED_PROJECTS
                )
            )
        ).scalars()
        if row.id not in allumes
    }
    return [
        ProjectOperationDto(
            connector=org_connector,
            operation=operation.name,
            access=operation.access,
            org_policy=operation.policy,
            project_policy=tightened.get(operation.id),
            effective_policy=stricter(operation.policy, tightened.get(operation.id)),
            description=operation.description,
            org_arguments=operation.arguments,
            project_arguments=narrowed_by.get(operation.id),
        )
        for operation, org_connector in op_rows
        if operation.connector_id not in closed_connectors
        and (not operation.groups or project_groups & set(operation.groups))
    ]


@router.get(
    "/projects/{id}/operations",
    response_model=list[ProjectOperationDto],
    operation_id="listProjectOperations",
)
async def list_project_operations(ctx: ProjectCtx, session: Db) -> list[ProjectOperationDto]:
    return await _project_operations(ctx, session)


@router.put(
    "/projects/{id}/operations/{connector}/{operation}",
    response_model=ProjectOperationDto,
    operation_id="tightenProjectOperation",
)
async def tighten_operation(
    ctx: ProjectCtx, connector: Name, operation: str, body: ProjectOperationPut, session: Db
) -> ProjectOperationDto:
    """A project TIGHTENS an operation; it never widens it (422)."""
    ctx.require(Permission.CONNECTOR_WRITE)
    visible_ops = {(o.connector, o.operation): o for o in await _project_operations(ctx, session)}
    actuelle = visible_ops.get((connector, operation))
    if actuelle is None:
        raise not_found("Operation", f"{connector}/{operation}")
    if RANK[body.policy] < RANK[actuelle.org_policy]:
        raise unprocessable(
            f"{connector}/{operation} is `{actuelle.org_policy}` in the organisation: a project can "
            f"only narrow it, not move it to `{body.policy}`"
        )
    op_row = (
        await session.execute(
            select(ConnectorOperation)
            .join(OrgConnector, OrgConnector.id == ConnectorOperation.connector_id)
            .where(
                OrgConnector.org_id == ctx.project.org_id,
                OrgConnector.name == connector,
                ConnectorOperation.name == operation,
            )
        )
    ).scalar_one()
    if body.arguments is not None:
        refuse_constraint(operation, op_row.input_schema, body.arguments, f"{connector}.{operation}")
    resserre = (
        await session.execute(
            select(ProjectOperationPolicy).where(
                ProjectOperationPolicy.project_id == ctx.id, ProjectOperationPolicy.operation_id == op_row.id
            )
        )
    ).scalar_one_or_none()
    if resserre is None:
        resserre = ProjectOperationPolicy(
            org_id=ctx.project.org_id, project_id=ctx.id, operation_id=op_row.id, policy=body.policy
        )
        session.add(resserre)
    resserre.policy = body.policy
    resserre.arguments = body.arguments
    await session.flush()
    await record(
        session,
        ctx.principal,
        "project.operation.tighten",
        org_id=ctx.project.org_id,
        target_type="connector_operation",
        target_id=op_row.id,
        connector=connector,
        operation=operation,
        policy=body.policy,
        arguments=body.arguments,
    )
    return ProjectOperationDto(
        connector=connector,
        operation=operation,
        access=op_row.access,
        org_policy=op_row.policy,
        project_policy=body.policy,
        effective_policy=stricter(op_row.policy, body.policy),
        description=op_row.description,
        org_arguments=op_row.arguments,
        project_arguments=body.arguments,
    )


@router.delete(
    "/projects/{id}/operations/{connector}/{operation}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="relaxProjectOperation",
)
async def relax_operation(ctx: ProjectCtx, connector: Name, operation: str, session: Db) -> Response:
    """Remove what the project tightened: the operation goes back to the organisation's policy."""
    ctx.require(Permission.CONNECTOR_WRITE)
    op_rows = (
        await session.execute(
            select(ProjectOperationPolicy)
            .join(ConnectorOperation, ConnectorOperation.id == ProjectOperationPolicy.operation_id)
            .join(OrgConnector, OrgConnector.id == ConnectorOperation.connector_id)
            .where(
                ProjectOperationPolicy.project_id == ctx.id,
                OrgConnector.name == connector,
                ConnectorOperation.name == operation,
            )
        )
    ).scalars()
    for op_row in op_rows:
        await session.delete(op_row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ───────────────────── enabling an organisation connector for a project (ADR 0074) ─────────────────────


def _on_for_all(connector: str, org: str) -> str:
    return f"`{connector}` is on for every project of {org}: its administrator restricts it first"


async def _project_dto(
    session: AsyncSession, ctx: ProjectCtx, connector: OrgConnector
) -> ProjectOrgConnectorDto:
    decision = decide(ctx.principal, ctx.org_slug, CatalogueAction.ENABLE, project_slug=ctx.slug)
    toggle = decision.allowed and connector.availability == SELECTED_PROJECTS
    reason = decision.reason
    if decision.allowed and not toggle:
        reason = _on_for_all(connector.name, ctx.org_slug)
    return ProjectOrgConnectorDto(
        name=connector.name,
        kind=connector.kind,
        type=connector.type,
        availability=connector.availability,
        enabled=await connector_enabled_for(session, connector, ctx.project),
        can_toggle=toggle,
        cannot_reason=None if toggle else reason,
    )


@router.get(
    "/projects/{id}/org-connectors",
    response_model=list[ProjectOrgConnectorDto],
    operation_id="listProjectOrgConnectors",
)
async def list_project_org_connectors(ctx: ProjectCtx, session: Db) -> list[ProjectOrgConnectorDto]:
    """The organisation's connectors and whether each is on for this project. Its members can read it."""
    rows = (
        await session.execute(
            select(OrgConnector).where(OrgConnector.org_id == ctx.project.org_id).order_by(OrgConnector.name)
        )
    ).scalars()
    return [await _project_dto(session, ctx, row) for row in rows]


async def _selected(ctx: ProjectCtx, session: Db, connector: str) -> OrgConnector:
    decision = decide(ctx.principal, ctx.org_slug, CatalogueAction.ENABLE, project_slug=ctx.slug)
    if not decision.allowed:
        raise forbidden(decision.reason or "refused")
    row = (
        await session.execute(
            select(OrgConnector).where(
                OrgConnector.org_id == ctx.project.org_id, OrgConnector.name == connector
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Connector", connector)
    if row.availability != SELECTED_PROJECTS:
        raise conflict(_on_for_all(connector, ctx.org_slug))
    return row


@router.put(
    "/projects/{id}/org-connectors/{connector}/enablement",
    response_model=ProjectOrgConnectorDto,
    operation_id="enableOrgConnector",
)
async def enable_org_connector(ctx: ProjectCtx, connector: Name, session: Db) -> ProjectOrgConnectorDto:
    """Turn an organisation connector on for this project (idempotent). Its owner or an administrator."""
    row = await _selected(ctx, session, connector)
    if not await connector_enabled_for(session, row, ctx.project):
        session.add(
            OrgConnectorProject(
                org_id=ctx.project.org_id,
                connector_id=row.id,
                project_id=ctx.id,
                enabled_by=ctx.principal.email,
            )
        )
        await session.flush()
        await record(
            session,
            ctx.principal,
            "org.connector.enable",
            org_id=ctx.project.org_id,
            target_type="org_connector",
            target_id=row.id,
            connector=connector,
            project=ctx.slug,
        )
    return await _project_dto(session, ctx, row)


@router.delete(
    "/projects/{id}/org-connectors/{connector}/enablement",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="disableOrgConnector",
)
async def disable_org_connector(ctx: ProjectCtx, connector: Name, session: Db) -> Response:
    """Turn it off for this project; turning off one that was not on is a no-op."""
    row = await _selected(ctx, session, connector)
    existing = (
        await session.execute(
            select(OrgConnectorProject).where(
                OrgConnectorProject.connector_id == row.id, OrgConnectorProject.project_id == ctx.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await record(
            session,
            ctx.principal,
            "org.connector.disable",
            org_id=ctx.project.org_id,
            target_type="org_connector",
            target_id=row.id,
            connector=connector,
            project=ctx.slug,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
