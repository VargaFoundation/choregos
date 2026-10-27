# SPDX-License-Identifier: Apache-2.0
"""Les connecteurs de l'organisation, et la politique de chaque opération (ADR 0034).

Un annuaire, un gestionnaire de parc, un serveur MCP : l'administrateur de l'organisation les
déclare une fois, nommés, et décide pour chaque opération si elle est permise, soumise à
validation (une action gouvernée, ADR 0035) ou interdite — et pour quels groupes de projets.
Un projet ne fait que RESSERRER : une opération `approval` ne devient jamais `allowed` en dessous
de l'organisation, et seul l'administrateur change les groupes (le second verrou de l'ADR 0014).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import (
    ConnectorOperation,
    Organization,
    OrgConnector,
    OrgConnectorProject,
    ProjectOperationPolicy,
)
from ..deps import Db, Me, ProjectCtx
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
from ..services.catalogue_rights import (
    SELECTED_PROJECTS,
    CatalogueAction,
    connector_enabled_for,
    decide,
    enabled_ids,
    enabled_projects_of,
    visible_projects,
)
from ..services.connecteurs import (
    RANG,
    check_type_config,
    declared_operation,
    plus_stricte,
    spec_du_type,
    verifier_les_secrets,
)
from ..services.connector_secrets import (
    drop_replaced_store_values,
    put_values,
    refs_with_values,
    require_store,
)
from ..services.egress import egress_gate_for_connector, egress_scope
from ..services.platform_network import (
    PlatformNetworkRefused,
    check_connector_declaration,
    enforce_connector_call,
    refusal_error,
)

router = APIRouter(tags=["connectors"])

Nom = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]


async def _organisation(session: AsyncSession, org: str) -> Organization:
    trouvee = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if trouvee is None:
        raise not_found("Organisation", org)
    return trouvee


def _administrer(principal: Me, org: str) -> None:
    """Déclarer un connecteur, ouvrir une opération, choisir ses groupes : décider de ce que
    l'organisation ouvre à ses projets. L'administrateur de l'organisation seul."""
    if not principal.can(Permission.TOOLS_GRANT, org):
        raise forbidden(f"the connectors of {org} and their operations are decided by its administrator")


async def _instance(session: AsyncSession, organisation: Organization, nom: str) -> OrgConnector:
    instance = (
        await session.execute(
            select(OrgConnector).where(OrgConnector.org_id == organisation.id, OrgConnector.name == nom)
        )
    ).scalar_one_or_none()
    if instance is None:
        raise not_found("Connector", nom)
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
    organisation = await _organisation(session, org)
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
    """Une instance naît avec les opérations que son type déclare, à leur politique par défaut :
    une lecture permise, une écriture soumise à validation."""
    from choregos_adapters import connector_types

    _administrer(principal, org)
    organisation = await _organisation(session, org)
    kind = body.kind
    if kind is None:
        sortes = sorted({k for k, t, _ in connector_types() if t == body.type})
        if len(sortes) != 1:
            raise unprocessable(f"`{body.type}`: specify the capability (`kind`) among {sortes or 'none'}")
        kind = sortes[0]
    return await declare_org_connector(
        session, principal, organisation, body.model_copy(update={"kind": kind})
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
    spec = spec_du_type(kind, type_name)
    check_type_config(kind, type_name, config)
    prefix = f"connector/{name}"
    verifier_les_secrets(
        spec,
        type_name,
        config,
        secret_refs,
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
    deja = (
        await session.execute(
            select(OrgConnector.id).where(OrgConnector.org_id == organisation.id, OrgConnector.name == name)
        )
    ).first()
    if deja is not None:
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
async def get_org_connector(org: str, name: Nom, session: Db, principal: Me) -> OrgConnectorDto:
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden(f"the connectors of {org} are readable by its members only")
    return await _dto(session, await _instance(session, await _organisation(session, org), name), principal)


@router.patch(
    "/orgs/{org}/connectors/{name}", response_model=OrgConnectorDto, operation_id="updateOrgConnector"
)
async def update_org_connector(
    org: str, name: Nom, body: OrgConnectorUpdate, session: Db, principal: Me
) -> OrgConnectorDto:
    """Edit what an administrator declared: the configuration, and the secrets. `secret_refs` and
    `secret_values` merge into the connector's; a field given as a reference after being a stored
    value drops the stored value. The operations are not touched (their own route)."""
    _administrer(principal, org)
    organisation = await _organisation(session, org)
    instance = await _instance(session, organisation, name)
    server = plugin_servers.spec_of_connector(instance)
    spec = server or spec_du_type(instance.kind, instance.type)
    prefix = f"connector/{name}"
    config = body.config if body.config is not None else dict(instance.config or {})
    if body.config is not None and server is None:
        check_type_config(instance.kind, instance.type, config)
    elif body.config is not None:
        # A plugin's server (ADR 0094): its own schema, and the hosts it would reach within the bounds.
        problems = plugin_servers.configuration_problems(instance.plugin_server or {}, config, partial=True)
        if problems:
            raise unprocessable(
                f"the configuration of the server `{name}` breaks its schema",
                [{"loc": ["body", "config"], "msg": p} for p in problems],
            )
        await plugin_servers.check_egress(session, instance, config)
    refs_in, values = dict(body.secret_refs or {}), dict(body.secret_values or {})
    verifier_les_secrets(spec, instance.type, config, refs_in, secret_values=values, store_prefix=prefix)
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
        if not decision.allowed:  # pragma: no cover - `_administrer` already refused
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
    name: Nom,
    field: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,63}$")],
    body: SecretValue,
    session: Db,
    principal: Me,
) -> OrgConnectorDto:
    """Type a secret once (or replace it): sealed, never returned. The response says it is set."""
    _administrer(principal, org)
    organisation = await _organisation(session, org)
    instance = await _instance(session, organisation, name)
    verifier_les_secrets(
        plugin_servers.spec_of_connector(instance) or spec_du_type(instance.kind, instance.type),
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
    name: Nom,
    field: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,63}$")],
    session: Db,
    principal: Me,
) -> OrgConnectorDto:
    """Remove a secret field's value or reference: the connector no longer holds it."""
    _administrer(principal, org)
    organisation = await _organisation(session, org)
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
async def delete_org_connector(org: str, name: Nom, session: Db, principal: Me) -> Response:
    _administrer(principal, org)
    organisation = await _organisation(session, org)
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
    org: str, name: Nom, operation: str, body: OperationPatch, session: Db, principal: Me
) -> OperationDto:
    """La politique, les groupes, le prix d'une opération : la décision de l'organisation."""
    _administrer(principal, org)
    organisation = await _organisation(session, org)
    instance = await _instance(session, organisation, name)
    ligne = (
        await session.execute(
            select(ConnectorOperation).where(
                ConnectorOperation.connector_id == instance.id, ConnectorOperation.name == operation
            )
        )
    ).scalar_one_or_none()
    if ligne is None:
        raise not_found("Operation", operation)
    if body.policy == "allowed":
        declared = declared_operation(instance, operation)
        if declared is not None and declared.human_approval:
            raise unprocessable(
                f"{name}/{operation} is approved by a person, always (its type declares it): "
                "`approval` or `forbidden` only"
            )
    if body.policy is not None:
        ligne.policy = body.policy
    if body.groups is not None:
        ligne.groups = sorted(set(body.groups))
    if "price_usd" in body.model_fields_set:
        ligne.price_usd = body.price_usd
    if "approver_group" in body.model_fields_set:
        if body.approver_group and ligne.access != "write":
            raise unprocessable(f"{name}/{operation} is a read: nobody approves it")
        ligne.approver_group = body.approver_group
    if "result_max_bytes" in body.model_fields_set:
        from ..config import get_settings

        ceiling = get_settings().broker_result_ceiling_bytes
        if body.result_max_bytes is not None and body.result_max_bytes > ceiling:
            raise unprocessable(
                f"{name}/{operation}: a result cap of {body.result_max_bytes} bytes is above the"
                f" platform's ceiling ({ceiling} bytes, CHOREGOS_BROKER_RESULT_CEILING_BYTES)"
            )
        ligne.result_max_bytes = body.result_max_bytes
    await record(
        session,
        principal,
        "org.connector.operation",
        org_id=organisation.id,
        target_type="connector_operation",
        target_id=ligne.id,
        connector=name,
        operation=operation,
        **body.model_dump(exclude_unset=True),
    )
    return OperationDto.model_validate(ligne)


@router.post(
    "/orgs/{org}/connectors/{name}/discover",
    response_model=ConnectorDiscovery,
    operation_id="discoverConnectorOperations",
)
async def discover_operations(org: str, name: Nom, session: Db, principal: Me) -> ConnectorDiscovery:
    """Demande au serveur ses outils et en tire le diff (ADR 0034).

    Un outil NOUVEAU naît fermé (`forbidden`) : un serveur n'ajoute pas une capacité dans le dos de
    l'administrateur. Un outil dont le schéma d'entrée DÉRIVE est refermé : un `commander_poste`
    qui accepte soudain une quantité n'est plus l'opération qu'on avait ouverte. Un outil que le
    serveur ne propose plus disparaît, avec ce que les projets en resserraient."""
    import httpx
    from choregos_adapters.mcp import ErreurMcp, empreinte_du_schema
    from choregos_core import utcnow
    from choregos_core.redaction import redact
    from choregos_core.secrets import SecretIntrouvable

    _administrer(principal, org)
    organisation = await _organisation(session, org)
    instance = await _instance(session, organisation, name)
    try:
        await enforce_connector_call(session, instance, polite=False)
    except PlatformNetworkRefused as refused:
        raise refusal_error(refused) from refused
    try:
        client: Any = await plugin_servers.connector_client(session, instance)
        lister = getattr(client, "list_tools", None)
        if lister is None:
            raise unprocessable(f"`{instance.type}` does not discover its operations: its type declares them")
        async with egress_scope(session, organisation, "api", f"org-connector:{name}"):
            outils = await lister()
    except (ErreurMcp, SecretIntrouvable, httpx.HTTPError) as panne:
        instance.status, instance.last_check_at = "error", utcnow()
        instance.last_error = redact(str(panne))[:1000]
        # La réponse est une erreur, et la requête serait annulée avec elle : l'état du connecteur,
        # lui, doit rester — l'écran dit pourquoi il est en panne. Rien ne suit cette validation.
        await session.commit()
        raise upstream(instance.name, redact(str(panne))[:500]) from panne
    existantes = {
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
    for outil in outils:
        empreinte = empreinte_du_schema(outil.input_schema)
        acces = "read" if outil.read_only else "write"
        if declared is not None:
            # A plugin's server (ADR 0094): its `operations` are the floor. A tool it does not declare
            # is never added; a declared one keeps its declared access, whatever the server hints.
            if outil.name not in declared:
                undeclared_tools.append(outil.name)
                continue
            acces = str(declared[outil.name]["access"])
        operation = existantes.pop(outil.name, None)
        if operation is None:
            session.add(
                ConnectorOperation(
                    org_id=organisation.id,
                    connector_id=instance.id,
                    name=outil.name,
                    access=acces,
                    policy="forbidden",
                    groups=[],
                    schema_digest=empreinte,
                    input_schema=outil.input_schema,
                    output_schema=getattr(outil, "output_schema", None),
                    description=outil.description or None,
                )
            )
            diff.added.append(outil.name)
            continue
        # The declared output schema follows the server on every discovery: it bounds what the agent
        # reads (#755), it does not change what the tool does, so it does not close the operation.
        operation.output_schema = getattr(outil, "output_schema", None)
        if declared is not None and operation.schema_digest is None:
            # A declared operation the server lists for the first time: its schema is learnt, not a
            # drift — the plugin's declaration opened it at its floor (ADR 0094).
            operation.schema_digest, operation.input_schema = empreinte, outil.input_schema
            operation.description = operation.description or outil.description or None
            diff.unchanged += 1
            continue
        if operation.schema_digest != empreinte or operation.access != acces:
            operation.policy, operation.schema_digest, operation.access = "forbidden", empreinte, acces
            operation.input_schema = outil.input_schema
            operation.description = outil.description or operation.description
            diff.changed.append(outil.name)
        else:
            diff.unchanged += 1
    for nom, operation in existantes.items():
        if declared is not None and nom in declared:
            continue  # declared by the plugin: kept even while the server does not list it
        await session.delete(operation)
        diff.removed.append(nom)
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


# ───────────────────────────── ce qu'un projet en voit, et resserre ─────────────────────────────


async def _operations_du_projet(ctx: ProjectCtx, session: AsyncSession) -> list[ProjectOperationDto]:
    """Les opérations ouvertes à ce projet : celles dont les groupes croisent les siens (aucun
    groupe : tous les projets), avec la politique de l'organisation, la sienne, et l'effective."""
    groupes = {str(g) for g in ((ctx.project.config or {}).get("groups") or [])}
    lignes = (
        await session.execute(
            select(ConnectorOperation, OrgConnector.name)
            .join(OrgConnector, OrgConnector.id == ConnectorOperation.connector_id)
            .where(ConnectorOperation.org_id == ctx.project.org_id)
            .order_by(OrgConnector.name, ConnectorOperation.name)
        )
    ).all()
    resserres = {
        p.operation_id: p.policy
        for p in (
            await session.execute(
                select(ProjectOperationPolicy).where(ProjectOperationPolicy.project_id == ctx.id)
            )
        ).scalars()
    }
    allumes = await enabled_ids(session, ctx.project)
    fermes = {
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
            connector=connecteur,
            operation=operation.name,
            access=operation.access,
            org_policy=operation.policy,
            project_policy=resserres.get(operation.id),
            effective_policy=plus_stricte(operation.policy, resserres.get(operation.id)),
            description=operation.description,
        )
        for operation, connecteur in lignes
        if operation.connector_id not in fermes and (not operation.groups or groupes & set(operation.groups))
    ]


@router.get(
    "/projects/{id}/operations",
    response_model=list[ProjectOperationDto],
    operation_id="listProjectOperations",
)
async def list_project_operations(ctx: ProjectCtx, session: Db) -> list[ProjectOperationDto]:
    return await _operations_du_projet(ctx, session)


@router.put(
    "/projects/{id}/operations/{connector}/{operation}",
    response_model=ProjectOperationDto,
    operation_id="tightenProjectOperation",
)
async def tighten_operation(
    ctx: ProjectCtx, connector: Nom, operation: str, body: ProjectOperationPut, session: Db
) -> ProjectOperationDto:
    """Un projet RESSERRE une opération ; il ne l'élargit jamais (422)."""
    ctx.require(Permission.CONNECTOR_WRITE)
    visibles = {(o.connector, o.operation): o for o in await _operations_du_projet(ctx, session)}
    actuelle = visibles.get((connector, operation))
    if actuelle is None:
        raise not_found("Operation", f"{connector}/{operation}")
    if RANG[body.policy] < RANG[actuelle.org_policy]:
        raise unprocessable(
            f"{connector}/{operation} is `{actuelle.org_policy}` in the organisation: a project can "
            f"only narrow it, not move it to `{body.policy}`"
        )
    ligne = (
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
    resserre = (
        await session.execute(
            select(ProjectOperationPolicy).where(
                ProjectOperationPolicy.project_id == ctx.id, ProjectOperationPolicy.operation_id == ligne.id
            )
        )
    ).scalar_one_or_none()
    if resserre is None:
        resserre = ProjectOperationPolicy(
            org_id=ctx.project.org_id, project_id=ctx.id, operation_id=ligne.id, policy=body.policy
        )
        session.add(resserre)
    resserre.policy = body.policy
    await session.flush()
    await record(
        session,
        ctx.principal,
        "project.operation.tighten",
        org_id=ctx.project.org_id,
        target_type="connector_operation",
        target_id=ligne.id,
        connector=connector,
        operation=operation,
        policy=body.policy,
    )
    return ProjectOperationDto(
        connector=connector,
        operation=operation,
        access=ligne.access,
        org_policy=ligne.policy,
        project_policy=body.policy,
        effective_policy=plus_stricte(ligne.policy, body.policy),
        description=ligne.description,
    )


@router.delete(
    "/projects/{id}/operations/{connector}/{operation}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="relaxProjectOperation",
)
async def relax_operation(ctx: ProjectCtx, connector: Nom, operation: str, session: Db) -> Response:
    """Retirer ce que le projet resserrait : l'opération revient à la politique de l'organisation."""
    ctx.require(Permission.CONNECTOR_WRITE)
    lignes = (
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
    for ligne in lignes:
        await session.delete(ligne)
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
async def enable_org_connector(ctx: ProjectCtx, connector: Nom, session: Db) -> ProjectOrgConnectorDto:
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
async def disable_org_connector(ctx: ProjectCtx, connector: Nom, session: Db) -> Response:
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
