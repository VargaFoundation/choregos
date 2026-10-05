# SPDX-License-Identifier: Apache-2.0
"""Les connecteurs de l'organisation, et la politique de chaque opération (ADR 0034).

Un annuaire, un gestionnaire de parc, un serveur MCP : l'administrateur de l'organisation les
déclare une fois, nommés, et décide pour chaque opération si elle est permise, soumise à
validation (une action gouvernée, ADR 0035) ou interdite — et pour quels groupes de projets.
Un projet ne fait que RESSERRER : une opération `approval` ne devient jamais `allowed` en dessous
de l'organisation, et seul l'administrateur change les groupes (le second verrou de l'ADR 0014).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import ConnectorOperation, Organization, OrgConnector, ProjectOperationPolicy
from ..deps import Db, Me, ProjectCtx
from ..errors import conflict, forbidden, not_found, unprocessable, upstream
from ..rbac import Permission
from ..schemas import (
    ConnectorDiscovery,
    OperationDto,
    OperationPatch,
    OrgConnectorCreate,
    OrgConnectorDto,
    ProjectOperationDto,
    ProjectOperationPut,
)
from ..services.connecteurs import RANG, plus_stricte, spec_du_type, verifier_les_secrets

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
        raise forbidden(f"les connecteurs de {org} et leurs opérations se décident par son administrateur")


async def _instance(session: AsyncSession, organisation: Organization, nom: str) -> OrgConnector:
    instance = (
        await session.execute(
            select(OrgConnector).where(OrgConnector.org_id == organisation.id, OrgConnector.name == nom)
        )
    ).scalar_one_or_none()
    if instance is None:
        raise not_found("Connecteur", nom)
    return instance


async def _dto(session: AsyncSession, instance: OrgConnector) -> OrgConnectorDto:
    operations = (
        await session.execute(
            select(ConnectorOperation)
            .where(ConnectorOperation.connector_id == instance.id)
            .order_by(ConnectorOperation.name)
        )
    ).scalars()
    dto = OrgConnectorDto.model_validate(instance)
    dto.operations = [OperationDto.model_validate(o) for o in operations]
    return dto


@router.get("/orgs/{org}/connectors", response_model=list[OrgConnectorDto], operation_id="listOrgConnectors")
async def list_org_connectors(org: str, session: Db, principal: Me) -> list[OrgConnectorDto]:
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden(f"les connecteurs de {org} se lisent par ses membres")
    organisation = await _organisation(session, org)
    instances = (
        await session.execute(
            select(OrgConnector).where(OrgConnector.org_id == organisation.id).order_by(OrgConnector.name)
        )
    ).scalars()
    return [await _dto(session, instance) for instance in instances]


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
            raise unprocessable(f"`{body.type}` : préciser la capacité (`kind`) parmi {sortes or 'aucune'}")
        kind = sortes[0]
    spec = spec_du_type(kind, body.type)
    verifier_les_secrets(spec, body.type, body.config, body.secret_refs)
    deja = (
        await session.execute(
            select(OrgConnector.id).where(
                OrgConnector.org_id == organisation.id, OrgConnector.name == body.name
            )
        )
    ).first()
    if deja is not None:
        raise conflict(f"un connecteur `{body.name}` existe déjà dans {org}")
    instance = OrgConnector(
        org_id=organisation.id,
        name=body.name,
        kind=kind,
        type=body.type,
        config=body.config,
        secret_refs=dict(body.secret_refs) or None,
        created_by=principal.email,
    )
    session.add(instance)
    await session.flush()
    for operation in spec.operations:
        session.add(
            ConnectorOperation(
                org_id=organisation.id,
                connector_id=instance.id,
                name=operation.name,
                access=operation.access,
                policy=operation.default_policy,
                groups=[],
                description=operation.description or None,
            )
        )
    await session.flush()
    await record(
        session,
        principal,
        "org.connector.create",
        org_id=organisation.id,
        target_type="org_connector",
        target_id=instance.id,
        name=body.name,
        type=body.type,
    )
    return await _dto(session, instance)


@router.get("/orgs/{org}/connectors/{name}", response_model=OrgConnectorDto, operation_id="getOrgConnector")
async def get_org_connector(org: str, name: Nom, session: Db, principal: Me) -> OrgConnectorDto:
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden(f"les connecteurs de {org} se lisent par ses membres")
    return await _dto(session, await _instance(session, await _organisation(session, org), name))


@router.delete(
    "/orgs/{org}/connectors/{name}", status_code=status.HTTP_204_NO_CONTENT, operation_id="deleteOrgConnector"
)
async def delete_org_connector(org: str, name: Nom, session: Db, principal: Me) -> Response:
    _administrer(principal, org)
    organisation = await _organisation(session, org)
    instance = await _instance(session, organisation, name)
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
        raise not_found("Opération", operation)
    if body.policy is not None:
        ligne.policy = body.policy
    if body.groups is not None:
        ligne.groups = sorted(set(body.groups))
    if "price_usd" in body.model_fields_set:
        ligne.price_usd = body.price_usd
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
    from choregos_adapters import build, configuration_resolue
    from choregos_adapters.mcp import ErreurMcp, empreinte_du_schema
    from choregos_core import utcnow
    from choregos_core.secrets import SecretIntrouvable

    _administrer(principal, org)
    organisation = await _organisation(session, org)
    instance = await _instance(session, organisation, name)
    try:
        config = configuration_resolue(instance.kind, instance.type, instance.config, instance.secret_refs)
        lister = getattr(build(instance.kind, instance.type, config), "list_tools", None)
        if lister is None:
            raise unprocessable(f"`{instance.type}` ne découvre pas ses opérations : son type les déclare")
        outils = await lister()
    except (ErreurMcp, SecretIntrouvable, httpx.HTTPError) as panne:
        instance.status, instance.last_check_at, instance.last_error = "error", utcnow(), str(panne)[:1000]
        # La réponse est une erreur, et la requête serait annulée avec elle : l'état du connecteur,
        # lui, doit rester — l'écran dit pourquoi il est en panne. Rien ne suit cette validation.
        await session.commit()
        raise upstream(instance.name, str(panne)[:500]) from panne
    existantes = {
        op.name: op
        for op in (
            await session.execute(
                select(ConnectorOperation).where(ConnectorOperation.connector_id == instance.id)
            )
        ).scalars()
    }
    diff = ConnectorDiscovery()
    for outil in outils:
        empreinte = empreinte_du_schema(outil.input_schema)
        acces = "read" if outil.read_only else "write"
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
                    description=outil.description or None,
                )
            )
            diff.added.append(outil.name)
        elif operation.schema_digest != empreinte or operation.access != acces:
            operation.policy, operation.schema_digest, operation.access = "forbidden", empreinte, acces
            operation.input_schema = outil.input_schema
            operation.description = outil.description or operation.description
            diff.changed.append(outil.name)
        else:
            diff.unchanged += 1
    for nom, operation in existantes.items():
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
        if not operation.groups or groupes & set(operation.groups)
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
        raise not_found("Opération", f"{connector}/{operation}")
    if RANG[body.policy] < RANG[actuelle.org_policy]:
        raise unprocessable(
            f"{connector}/{operation} est `{actuelle.org_policy}` dans l'organisation : un projet ne "
            f"fait que resserrer, il ne passe pas à `{body.policy}`"
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
