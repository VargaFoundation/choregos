# SPDX-License-Identifier: Apache-2.0
"""Connecteurs : configuration, test de connexion, catalogue des types."""

from __future__ import annotations

from typing import Annotated

from choregos_core import utcnow
from fastapi import APIRouter, Path
from sqlalchemy import select

from ..audit import record
from ..config import get_settings
from ..db.models import Connector
from ..deps import Db, ProjectCtx
from ..errors import not_found, unprocessable
from ..rbac import Permission
from ..schemas import (
    ConnectorCheck,
    ConnectorDto,
    ConnectorTestResult,
    ConnectorType,
    ConnectorUpsert,
    ProjectRequirement,
)

router = APIRouter(tags=["connectors"])


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
    return [ConnectorDto.model_validate(row) for row in rows]


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

    verifier_les_secrets(
        spec_du_type(kind, body.type), body.type, body.config, body.secret_refs, body.secret_ref
    )
    row = (
        await session.execute(select(Connector).where(Connector.project_id == ctx.id, Connector.kind == kind))
    ).scalar_one_or_none()
    if row is None:
        row = Connector(project_id=ctx.id, kind=kind, type=body.type)
        session.add(row)
    row.type = body.type
    row.config = body.config
    row.secret_ref = body.secret_ref
    row.secret_refs = dict(body.secret_refs) or None
    row.status = "unknown"
    await session.flush()
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
    return ConnectorDto.model_validate(row)


@router.get(
    "/projects/{id}/requirements",
    response_model=list[ProjectRequirement],
    operation_id="listProjectRequirements",
)
async def project_requirements(ctx: ProjectCtx, session: Db) -> list[ProjectRequirement]:
    """Ce que les workflows actifs du projet exigent de ses connecteurs, et pourquoi (ADR 0034) :
    un projet sans dépôt ni train n'a rien à faire d'un `scm`, d'une `ci` ou d'un `cd`."""
    from choregos_adapters import type_par_defaut
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
            connector=ConnectorDto.model_validate(configures[e.capacite])
            if e.capacite in configures
            else None,
            default_type=None if e.capacite in configures else type_par_defaut(e.capacite),
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

    from choregos_adapters import build, configuration_resolue, fakes_enabled

    checks: list[ConnectorCheck] = []
    try:
        # Les secrets RÉSOLUS ici, dans ce processus : une référence qu'il ne sait pas lire est
        # un échec de test qui la nomme, pas un adaptateur qui retombe sur sa valeur par défaut.
        config = configuration_resolue(kind, row.type, row.config, row.secret_refs, row.secret_ref)
        adapter = build(kind, row.type, config)
        checks.append(ConnectorCheck(name="construction", ok=True, detail=type(adapter).__name__))
        tester = getattr(adapter, "test", None)
        if tester is not None:
            result = await tester()
            checks.append(
                ConnectorCheck(name="connection", ok=bool(result.get("ok", True)), detail=str(result))
            )
        else:
            checks.append(
                ConnectorCheck(
                    name="connection",
                    ok=True,
                    detail="fakes active" if fakes_enabled() else "no specific test for this type",
                )
            )
    except Exception as exc:  # une erreur de connecteur n'est jamais une erreur 500
        checks.append(ConnectorCheck(name="connection", ok=False, detail=str(exc)[:500]))

    ok = all(check.ok for check in checks)
    row.status = "ok" if ok else "error"
    row.last_check_at = utcnow()
    row.last_error = None if ok else "; ".join(c.detail or "" for c in checks if not c.ok)[:1000]
    return ConnectorTestResult(ok=ok, checks=checks)
