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
from ..schemas import ConnectorCheck, ConnectorDto, ConnectorTestResult, ConnectorType, ConnectorUpsert

router = APIRouter(tags=["connectors"])

CONNECTOR_TYPES: list[ConnectorType] = [
    ConnectorType(
        kind="tracker",
        type="github-issues",
        display="GitHub Issues + Projects v2",
        config_schema={
            "type": "object",
            "required": ["repo"],
            "properties": {
                "repo": {"type": "string", "description": "owner/repo"},
                "project_number": {"type": "integer"},
                "installation_id": {"type": "integer"},
                "labels_prefix": {"type": "string", "default": "choregos:"},
            },
        },
    ),
    ConnectorType(
        kind="tracker",
        type="jira",
        display="Jira Cloud",
        available=False,
        config_schema={
            "type": "object",
            "properties": {"site": {"type": "string"}, "project_key": {"type": "string"}},
        },
    ),
    ConnectorType(
        kind="tracker",
        type="gitlab-issues",
        display="GitLab Issues",
        available=False,
        config_schema={"type": "object", "properties": {"project": {"type": "string"}}},
    ),
    ConnectorType(
        kind="scm",
        type="github",
        display="GitHub (App choregos-bot)",
        config_schema={
            "type": "object",
            "required": ["repo"],
            "properties": {
                "repo": {"type": "string"},
                "installation_id": {"type": "integer"},
                "merge_queue": {"type": "boolean", "default": True},
            },
        },
    ),
    ConnectorType(
        kind="ci",
        type="tekton",
        display="Tekton Pipelines",
        config_schema={
            "type": "object",
            "properties": {"namespace": {"type": "string"}, "pipeline": {"type": "string", "default": "ci"}},
        },
    ),
    ConnectorType(
        kind="cd",
        type="argocd",
        display="Argo CD + Rollouts",
        config_schema={
            "type": "object",
            "required": ["gitops_repo"],
            "properties": {
                "gitops_repo": {"type": "string"},
                "base_url": {"type": "string"},
                "app_pattern": {"type": "string", "default": "{app}-{env}"},
            },
        },
    ),
    ConnectorType(
        kind="runtime",
        type="tekton",
        display="Tekton PipelineRun (exécuteur)",
        config_schema={
            "type": "object",
            "properties": {"namespace": {"type": "string"}, "runner_image": {"type": "string"}},
        },
    ),
    ConnectorType(
        kind="runtime",
        type="k8s_job",
        display="Job Kubernetes (repli)",
        config_schema={"type": "object", "properties": {"namespace": {"type": "string"}}},
    ),
    ConnectorType(
        kind="runtime",
        type="local_docker",
        display="Docker local (dev)",
        config_schema={"type": "object", "properties": {"image": {"type": "string"}}},
    ),
    ConnectorType(
        kind="memory",
        type="ecphoria",
        display="Ecphoria (mémoire + base de connaissance)",
        config_schema={
            "type": "object",
            "properties": {"base_url": {"type": "string"}, "tenant": {"type": "string"}},
        },
    ),
    ConnectorType(
        kind="memory",
        type="pgvector",
        display="pgvector (repli)",
        config_schema={"type": "object", "properties": {}},
    ),
    ConnectorType(
        kind="gateway",
        type="litellm",
        display="LiteLLM",
        config_schema={
            "type": "object",
            "properties": {"base_url": {"type": "string"}, "team_id": {"type": "string"}},
        },
    ),
    ConnectorType(
        kind="notify",
        type="slack",
        display="Slack",
        config_schema={
            "type": "object",
            "properties": {"channel": {"type": "string"}, "webhook_url": {"type": "string"}},
        },
    ),
]


@router.get("/connectors/types", response_model=list[ConnectorType], operation_id="listConnectorTypes")
async def list_connector_types() -> list[ConnectorType]:
    return CONNECTOR_TYPES


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
            "passerelle `direct` refusée sur cet environnement : elle ne mesure aucun coût et "
            "n'applique aucun plafond. Utiliser `litellm`."
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
    row.status = "unknown"
    await session.flush()
    await record(
        session,
        ctx.principal,
        "connector.upsert",
        target_type="connector",
        target_id=row.id,
        kind=kind,
        type=body.type,
    )
    return ConnectorDto.model_validate(row)


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
        raise not_found("Connecteur", kind)

    from choregos_adapters import build, fakes_enabled

    checks: list[ConnectorCheck] = []
    try:
        adapter = build(kind, row.type, row.config)
        checks.append(ConnectorCheck(name="construction", ok=True, detail=type(adapter).__name__))
        tester = getattr(adapter, "test", None)
        if tester is not None:
            result = await tester()
            checks.append(
                ConnectorCheck(name="connexion", ok=bool(result.get("ok", True)), detail=str(result))
            )
        else:
            checks.append(
                ConnectorCheck(
                    name="connexion",
                    ok=True,
                    detail="fakes actifs" if fakes_enabled() else "aucun test spécifique fourni",
                )
            )
    except Exception as exc:  # une erreur de connecteur n'est jamais une erreur 500
        checks.append(ConnectorCheck(name="connexion", ok=False, detail=str(exc)[:500]))

    ok = all(check.ok for check in checks)
    row.status = "ok" if ok else "error"
    row.last_check_at = utcnow()
    row.last_error = None if ok else "; ".join(c.detail or "" for c in checks if not c.ok)[:1000]
    return ConnectorTestResult(ok=ok, checks=checks)
