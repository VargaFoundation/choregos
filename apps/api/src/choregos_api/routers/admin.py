"""Administration plateforme : backends agents, exécuteurs, clés gateway, membres, audit."""

from __future__ import annotations

from typing import Annotated

from choregos_contracts import Role
from fastapi import APIRouter, Query, status
from sqlalchemy import select

from ..audit import record
from ..db.models import (
    AuditLog,
    BackendRegistryRow,
    ExecutorRow,
    GatewayKeyRow,
    Membership,
    Organization,
    Project,
    User,
)
from ..deps import Db, Me, Pagination
from ..errors import forbidden, not_found
from ..schemas import (
    AgentBackendInfo,
    AgentBackendUpdate,
    AuditEntry,
    AuditPage,
    ConformanceReport,
    ExecutorInfo,
    GatewayKeyInfo,
    MembershipDto,
    MembershipUpsert,
    PageMeta,
)

router = APIRouter(tags=["admin"])

KNOWN_BACKENDS = [
    ("claude-code", ["acp", "mcp", "agents_md", "structured_output"]),
    ("codex", ["acp", "mcp", "agents_md"]),
    ("gemini-cli", ["acp", "mcp"]),
    ("goose", ["acp", "mcp"]),
    ("opencode", ["acp", "mcp"]),
    ("copilot-cli", ["acp"]),
]


def _require_admin(principal: Me) -> None:
    if not principal.is_platform_admin():
        raise forbidden("réservé aux administrateurs de la plateforme")


@router.get("/platform/backends", response_model=list[AgentBackendInfo], operation_id="listBackends")
async def list_backends(session: Db, principal: Me) -> list[AgentBackendInfo]:
    rows = {row.name: row for row in (await session.execute(select(BackendRegistryRow))).scalars().all()}
    out: list[AgentBackendInfo] = []
    for name, capabilities in KNOWN_BACKENDS:
        row = rows.get(name)
        out.append(
            AgentBackendInfo(
                name=name,
                enabled=row.enabled if row else name == "claude-code",
                version=row.version if row else None,
                capabilities=list(row.capabilities) if row and row.capabilities else capabilities,
                conformance=ConformanceReport.model_validate(row.conformance)
                if row and row.conformance
                else ConformanceReport(),
                disabled_reason=row.disabled_reason if row else None,
            )
        )
    return out


@router.put("/platform/backends", response_model=AgentBackendInfo, operation_id="putBackend")
async def put_backend(body: AgentBackendUpdate, session: Db, principal: Me) -> AgentBackendInfo:
    _require_admin(principal)
    row = (
        await session.execute(select(BackendRegistryRow).where(BackendRegistryRow.name == body.name))
    ).scalar_one_or_none()
    if row is None:
        row = BackendRegistryRow(name=body.name, capabilities=[])
        session.add(row)
    row.enabled = body.enabled
    row.disabled_reason = body.disabled_reason
    await session.flush()
    await record(
        session, principal, "backend.update", target_type="backend", target_id=row.name, enabled=body.enabled
    )
    return AgentBackendInfo(
        name=row.name,
        enabled=row.enabled,
        version=row.version,
        capabilities=list(row.capabilities or []),
        conformance=ConformanceReport.model_validate(row.conformance or {}),
        disabled_reason=row.disabled_reason,
    )


@router.get("/platform/executors", response_model=list[ExecutorInfo], operation_id="listExecutors")
async def list_executors(session: Db, principal: Me) -> list[ExecutorInfo]:
    rows = (await session.execute(select(ExecutorRow))).scalars().all()
    if rows:
        return [
            ExecutorInfo(
                kind=row.kind,
                enabled=row.enabled,
                cluster=row.cluster,
                namespace_pattern=row.namespace_pattern,
                runner_image=row.runner_image,
                default_timeout_minutes=row.default_timeout_minutes,
            )
            for row in rows
        ]
    return [
        ExecutorInfo(kind="tekton", enabled=True, namespace_pattern="proj-{slug}-runners"),
        ExecutorInfo(kind="k8s_job", enabled=True, namespace_pattern="proj-{slug}-runners"),
        ExecutorInfo(kind="local_docker", enabled=False),
        ExecutorInfo(kind="aca", enabled=False),
    ]


@router.put("/platform/executors", response_model=ExecutorInfo, operation_id="putExecutor")
async def put_executor(body: ExecutorInfo, session: Db, principal: Me) -> ExecutorInfo:
    _require_admin(principal)
    row = (
        await session.execute(select(ExecutorRow).where(ExecutorRow.kind == body.kind))
    ).scalar_one_or_none()
    if row is None:
        row = ExecutorRow(kind=body.kind)
        session.add(row)
    row.enabled = body.enabled
    row.cluster = body.cluster
    row.namespace_pattern = body.namespace_pattern
    row.runner_image = body.runner_image
    row.default_timeout_minutes = body.default_timeout_minutes
    await record(session, principal, "executor.update", target_type="executor", target_id=body.kind)
    return body


@router.get("/platform/gateway/keys", response_model=list[GatewayKeyInfo], operation_id="listGatewayKeys")
async def list_gateway_keys(session: Db, principal: Me) -> list[GatewayKeyInfo]:
    _require_admin(principal)
    rows = (
        (await session.execute(select(GatewayKeyRow).order_by(GatewayKeyRow.created_at.desc()).limit(200)))
        .scalars()
        .all()
    )
    slug_rows = (await session.execute(select(Project.id, Project.slug))).all()
    slugs: dict[str, str] = {str(row[0]): str(row[1]) for row in slug_rows}
    return [
        GatewayKeyInfo(
            key_id=row.key_id,
            run_id=row.run_id,
            project_slug=slugs.get(row.project_id or ""),
            budget_usd=row.budget_usd,
            spend_usd=row.spend_usd,
            expires_at=row.expires_at,
            revoked=row.revoked,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.get("/orgs/{org}/members", response_model=list[MembershipDto], operation_id="listMembers")
async def list_members(org: str, session: Db, principal: Me) -> list[MembershipDto]:
    organization = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organization is None:
        raise not_found("Organisation", org)
    rows = (
        await session.execute(
            select(Membership, User, Project)
            .join(User, Membership.user_id == User.id)
            .outerjoin(Project, Membership.project_id == Project.id)
            .where(Membership.org_id == organization.id)
        )
    ).all()
    return [
        MembershipDto(
            user_id=user.id,
            email=user.email,
            org=org,
            project_slug=project.slug if project else None,
            role=Role(membership.role),
        )
        for membership, user, project in rows
    ]


@router.post(
    "/orgs/{org}/members",
    response_model=MembershipDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="addMember",
)
async def add_member(org: str, body: MembershipUpsert, session: Db, principal: Me) -> MembershipDto:
    organization = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organization is None:
        raise not_found("Organisation", org)
    from ..rbac import Permission

    if not principal.can(Permission.MEMBER_MANAGE, org, body.project_slug):
        raise forbidden("gérer les membres demande le rôle project_owner ou org_admin")
    user = (await session.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user is None:
        user = User(email=body.email, display_name=body.email.split("@")[0])
        session.add(user)
        await session.flush()
    project = None
    if body.project_slug:
        project = (
            await session.execute(select(Project).where(Project.slug == body.project_slug))
        ).scalar_one_or_none()
        if project is None:
            raise not_found("Projet", body.project_slug)
    existing = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.org_id == organization.id,
                Membership.project_id == (project.id if project else None),
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = Membership(
            user_id=user.id,
            org_id=organization.id,
            project_id=project.id if project else None,
            role=str(body.role),
        )
        session.add(existing)
    else:
        existing.role = str(body.role)
    await record(session, principal, "member.add", target_type="user", target_id=user.id, role=str(body.role))
    return MembershipDto(
        user_id=user.id, email=user.email, org=org, project_slug=body.project_slug, role=body.role
    )


@router.get("/audit", response_model=AuditPage, operation_id="listAudit")
async def list_audit(
    session: Db,
    principal: Me,
    page: Pagination,
    actor: Annotated[str | None, Query()] = None,
    target_type: Annotated[str | None, Query()] = None,
) -> AuditPage:
    from ..rbac import Permission

    if (
        not any(principal.can(Permission.AUDIT_READ, org) for org in principal.org_roles)
        and not principal.is_platform_admin()
    ):
        raise forbidden("lecture de l'audit réservée aux propriétaires et administrateurs")
    query = select(AuditLog).order_by(AuditLog.ts.desc())
    if actor:
        query = query.where(AuditLog.actor_id == actor)
    if target_type:
        query = query.where(AuditLog.target_type == target_type)
    rows = (await session.execute(query.limit(1000))).scalars().all()
    window, cursor, has_more = page.slice(list(rows))
    return AuditPage(
        items=[AuditEntry.model_validate(row) for row in window],
        meta=PageMeta(next_cursor=cursor, has_more=has_more),
    )
