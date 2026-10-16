# SPDX-License-Identifier: Apache-2.0
"""Connecteurs : configuration, test de connexion, catalogue des types."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..config import get_settings
from ..db.models import Connector
from ..deps import Db, ProjectContext, ProjectCtx
from ..errors import not_found, unprocessable
from ..rbac import Permission
from ..schemas import (
    ConnectorDto,
    ConnectorTestResult,
    ConnectorType,
    ConnectorUpsert,
    ProjectRequirement,
    SecretValue,
)
from ..secret_store import derived_project_name, secret_states
from ..services.connector_health import check_connector
from ..services.connector_secrets import (
    drop_replaced_store_values,
    put_values,
    refs_with_values,
    require_store,
)

router = APIRouter(tags=["connectors"])

Field = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


def _prefix(ctx: ProjectContext, kind: str) -> str:
    """`project/<slug>/<kind>`: the name a project connector's sealed secrets derive from."""
    return derived_project_name(ctx.project.slug, kind, "")[:-1]


async def _audit_secrets(
    session: AsyncSession,
    ctx: ProjectContext,
    row: Connector,
    action: str,
    fields_versions: dict[str, int | None],
) -> None:
    """Who set or cleared which field, at which version — never the value."""
    for field, version in fields_versions.items():
        await record(
            session,
            ctx.principal,
            action,
            org_id=ctx.project.org_id,
            target_type="connector",
            target_id=row.id,
            kind=row.kind,
            field=field,
            version=version,
        )


async def _dto(session: AsyncSession, ctx: ProjectContext, row: Connector) -> ConnectorDto:
    dto = ConnectorDto.model_validate(row)
    refs = dict(row.secret_refs or {})
    dto.secrets = await secret_states(session, ctx.project.org_id, refs)
    return dto


@router.get("/connectors/types", response_model=list[ConnectorType], operation_id="listConnectorTypes")
async def list_connector_types() -> list[ConnectorType]:
    """Les types que le REGISTRE connaît — ceux du cœur et ceux des greffons (ADR 0034). La liste
    vivait ici, en double du registre et en désaccord avec lui : jira y était « indisponible »
    alors qu'il est enregistré, et un type ajouté par un greffon n'y apparaissait jamais."""
    from choregos_adapters import connector_types

    return [
        ConnectorType(
            kind=kind,
            type=type_name,
            display=spec.display,
            config_schema=spec.config_schema,
            capabilities=list(spec.capabilities),
            secret_fields=list(spec.secret_fields),
        )
        for kind, type_name, spec in connector_types()
    ]


@router.get("/projects/{id}/connectors", response_model=list[ConnectorDto], operation_id="listConnectors")
async def list_connectors(ctx: ProjectCtx, session: Db) -> list[ConnectorDto]:
    rows = (
        (
            await session.execute(
                select(Connector).where(Connector.project_id == ctx.id).order_by(Connector.kind)
            )
        )
        .scalars()
        .all()
    )
    return [await _dto(session, ctx, row) for row in rows]


@router.put("/projects/{id}/connectors/{kind}", response_model=ConnectorDto, operation_id="putConnector")
async def put_connector(
    ctx: ProjectCtx,
    kind: Annotated[str, Path()],
    body: ConnectorUpsert,
    session: Db,
) -> ConnectorDto:
    ctx.require(Permission.CONNECTOR_WRITE)
    if kind == "gateway" and body.type == "direct" and get_settings().env in {"staging", "prod"}:
        # La passerelle directe rend la clé qu'on lui a donnée : aucune clé virtuelle par run,
        # aucun plafond dur, aucun coût mesuré. Le banc a tourné ainsi une semaine et
        # « prouvait » un plafond de dépense qui n'avait jamais mesuré une dépense. La fabrique
        # d'adaptateur la refuse déjà ; on refuse aussi de l'ÉCRIRE, pour que l'erreur arrive
        # quand quelqu'un la choisit, pas au premier run de la nuit suivante.
        raise unprocessable(
            "the `direct` gateway is refused in this environment: it measures no cost and "
            "enforces no cap. Use `litellm`."
        )
    from ..services.connecteurs import spec_du_type, verifier_les_secrets

    prefix = _prefix(ctx, kind)
    verifier_les_secrets(
        spec_du_type(kind, body.type),
        body.type,
        body.config,
        body.secret_refs,
        body.secret_ref,
        may_reference=ctx.principal.can(Permission.TOOLS_GRANT, ctx.org_slug),
        secret_values=body.secret_values,
        store_prefix=prefix,
    )
    store = require_store(body.secret_values)
    row = (
        await session.execute(select(Connector).where(Connector.project_id == ctx.id, Connector.kind == kind))
    ).scalar_one_or_none()
    if row is None:
        row = Connector(project_id=ctx.id, kind=kind, type=body.type)
        session.add(row)
    spec = spec_du_type(kind, body.type)
    before = dict(row.secret_refs or {})
    # A PUT replaces the references, but a sealed value is kept unless this body overrides its field
    # or the type no longer has it: re-saving the configuration must not wipe a typed secret.
    kept = {f: r for f, r in before.items() if r.startswith("store:") and f in spec.secret_fields}
    after = refs_with_values({**kept, **body.secret_refs}, prefix, body.secret_values)
    metas = await put_values(
        session, store, ctx.project.org_id, prefix, body.secret_values, ctx.principal.email
    )
    cleared = await drop_replaced_store_values(session, store, ctx.project.org_id, before, after)
    row.type = body.type
    row.config = body.config
    row.secret_ref = body.secret_ref
    row.secret_refs = after or None
    row.status = "unknown"
    await session.flush()
    # Saving tests (S40-05): a failing test is kept on the card, it does not refuse the save.
    await check_connector(session, ctx, row)
    await record(
        session,
        ctx.principal,
        "connector.upsert",
        org_id=ctx.project.org_id,
        target_type="connector",
        target_id=row.id,
        kind=kind,
        type=body.type,
    )
    await _audit_secrets(session, ctx, row, "connector.secret.set", {f: m.version for f, m in metas.items()})
    await _audit_secrets(session, ctx, row, "connector.secret.clear", dict.fromkeys(cleared))
    return await _dto(session, ctx, row)


async def _connector_row(ctx: ProjectContext, kind: str, session: AsyncSession) -> Connector:
    row = (
        await session.execute(select(Connector).where(Connector.project_id == ctx.id, Connector.kind == kind))
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Connector", kind)
    return row


@router.put(
    "/projects/{id}/connectors/{kind}/secrets/{field}",
    response_model=ConnectorDto,
    operation_id="setConnectorSecret",
)
async def set_connector_secret(
    ctx: ProjectCtx, kind: Annotated[str, Path()], field: Field, body: SecretValue, session: Db
) -> ConnectorDto:
    """Type a secret once (or replace it): sealed, never returned."""
    from ..services.connecteurs import spec_du_type, verifier_les_secrets

    ctx.require(Permission.CONNECTOR_WRITE)
    row = await _connector_row(ctx, kind, session)
    prefix = _prefix(ctx, kind)
    verifier_les_secrets(
        spec_du_type(kind, row.type),
        row.type,
        {},
        {},
        secret_values={field: body.value},
        store_prefix=prefix,
    )
    store = require_store({field: body.value})
    metas = await put_values(
        session, store, ctx.project.org_id, prefix, {field: body.value}, ctx.principal.email
    )
    row.secret_refs = refs_with_values(row.secret_refs, prefix, {field: body.value})
    row.status = "unknown"
    await session.flush()
    await check_connector(session, ctx, row)
    await _audit_secrets(session, ctx, row, "connector.secret.set", {field: metas[field].version})
    return await _dto(session, ctx, row)


@router.delete(
    "/projects/{id}/connectors/{kind}/secrets/{field}",
    response_model=ConnectorDto,
    operation_id="clearConnectorSecret",
)
async def clear_connector_secret(
    ctx: ProjectCtx, kind: Annotated[str, Path()], field: Field, session: Db
) -> ConnectorDto:
    """Remove a secret field's value or reference: the connector no longer holds it."""
    ctx.require(Permission.CONNECTOR_WRITE)
    row = await _connector_row(ctx, kind, session)
    before = dict(row.secret_refs or {})
    if field not in before:
        raise not_found("Secret", field)
    after = {f: r for f, r in before.items() if f != field}
    await drop_replaced_store_values(session, require_store(None), ctx.project.org_id, before, after)
    row.secret_refs = after or None
    row.status = "unknown"
    await session.flush()
    await check_connector(session, ctx, row)
    await _audit_secrets(session, ctx, row, "connector.secret.clear", {field: None})
    return await _dto(session, ctx, row)


@router.get(
    "/projects/{id}/requirements",
    response_model=list[ProjectRequirement],
    operation_id="listProjectRequirements",
)
async def project_requirements(ctx: ProjectCtx, session: Db) -> list[ProjectRequirement]:
    """Ce que les workflows actifs du projet exigent de ses connecteurs, et pourquoi (ADR 0034) :
    un projet sans dépôt ni train n'a rien à faire d'un `scm`, d'une `ci` ou d'un `cd`."""
    from choregos_adapters import deployment_default_type
    from choregos_core.dsl.exigences import exigences

    from ..db.models import WorkflowDef
    from ..services.definitions import workflow_model

    actifs = (
        await session.execute(
            select(WorkflowDef).where(WorkflowDef.project_id == ctx.id, WorkflowDef.is_active.is_(True))
        )
    ).scalars()
    configures = {
        c.kind: c
        for c in (await session.execute(select(Connector).where(Connector.project_id == ctx.id))).scalars()
    }
    return [
        ProjectRequirement(
            capability=e.capacite,
            reasons=list(e.raisons),
            connector=await _dto(session, ctx, configures[e.capacite]) if e.capacite in configures else None,
            default_type=None if e.capacite in configures else deployment_default_type(e.capacite),
        )
        for e in exigences(workflow_model(row) for row in actifs)
    ]


@router.post(
    "/projects/{id}/connectors/{kind}/test", response_model=ConnectorTestResult, operation_id="testConnector"
)
async def test_connector(ctx: ProjectCtx, kind: Annotated[str, Path()], session: Db) -> ConnectorTestResult:
    """Teste la connexion et les permissions du connecteur ; met à jour son état de santé."""
    ctx.require(Permission.CONNECTOR_WRITE)
    row = (
        await session.execute(select(Connector).where(Connector.project_id == ctx.id, Connector.kind == kind))
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Connector", kind)

    return await check_connector(session, ctx, row)
