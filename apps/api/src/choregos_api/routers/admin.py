# SPDX-License-Identifier: Apache-2.0
"""Administration plateforme : backends agents, exécuteurs, clés gateway, membres, audit."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from choregos_adapters.registry import default_executor_kind
from choregos_contracts import Role
from choregos_core import utcnow
from fastapi import APIRouter, Query, Response, status
from sqlalchemy import func, or_, select

from ..audit import record
from ..db.models import (
    AuditLog,
    BackendRegistryRow,
    ExecutorRow,
    GatewayKeyRow,
    Membership,
    NetworkDecision,
    Organization,
    Project,
    User,
)
from ..db.session import en_portee_de_plateforme
from ..deps import Db, Me, Pagination, exiger_admin_de_plateforme
from ..errors import conflict, forbidden, not_found, unprocessable
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
    SecretsRewrap,
)
from ..schemas.network import NetworkActivity, NetworkActivityRow

router = APIRouter(tags=["admin"])

KNOWN_BACKENDS = [
    ("claude-code", ["acp", "mcp", "agents_md", "structured_output"]),
    ("codex", ["acp", "mcp", "agents_md"]),
    ("gemini-cli", ["acp", "mcp"]),
    ("goose", ["acp", "mcp"]),
    ("opencode", ["acp", "mcp"]),
    ("copilot-cli", ["acp"]),
]


@router.post("/platform/secrets/rewrap", response_model=SecretsRewrap, operation_id="rewrapSecrets")
async def rewrap_secrets(session: Db, principal: Me) -> SecretsRewrap:
    """After a KEK rotation (the new key first in `CHOREGOS_SECRETS_KEKS`): re-wrap every sealed
    secret's data key under the active key. The values are never read out nor changed (ADR 0058)."""
    from ..secret_store import SealedDbSecretStore, SecretStoreUnavailable

    await exiger_admin_de_plateforme(session, principal)
    try:
        async with en_portee_de_plateforme(session):
            rewrapped = await SealedDbSecretStore().rewrap(session)
    except SecretStoreUnavailable as refus:
        raise conflict(str(refus)) from refus
    await record(
        session, principal, "org.secret.rewrap", org_id=None, target_type="org_secret", rewrapped=rewrapped
    )
    return SecretsRewrap(rewrapped=rewrapped)


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
    await exiger_admin_de_plateforme(session, principal)
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
        session,
        principal,
        "backend.update",
        org_id=None,  # le catalogue de backends est de plateforme
        target_type="backend",
        target_id=row.name,
        enabled=body.enabled,
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
        # On when the deployment runs its agents through it (the local install, `compose.yaml`).
        ExecutorInfo(kind="local_docker", enabled=default_executor_kind() == "local_docker"),
        ExecutorInfo(kind="aca", enabled=False),
        # Opt-in: needs the agent-sandbox operator, installed by a platform team (ADR 0016).
        ExecutorInfo(kind="agent_sandbox", enabled=False),
    ]


@router.put("/platform/executors", response_model=ExecutorInfo, operation_id="putExecutor")
async def put_executor(body: ExecutorInfo, session: Db, principal: Me) -> ExecutorInfo:
    await exiger_admin_de_plateforme(session, principal)
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
    await record(
        session,
        principal,
        "executor.update",
        org_id=None,  # idem
        target_type="executor",
        target_id=body.kind,
    )
    return body


@router.get("/platform/gateway/keys", response_model=list[GatewayKeyInfo], operation_id="listGatewayKeys")
async def list_gateway_keys(session: Db, principal: Me) -> list[GatewayKeyInfo]:
    await exiger_admin_de_plateforme(session, principal)
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
async def list_members(
    org: str, session: Db, principal: Me, project: Annotated[str | None, Query()] = None
) -> list[MembershipDto]:
    """Who belongs to the organisation, or to one of its projects with `?project=`.

    S26-04: any signed-in user could list any organisation. A role in the organisation reads every
    membership; a role on some of its projects only reads the memberships of those projects; anyone
    else gets 403 — before the lookup, so an organisation's existence is not told to a stranger (as
    `/orgs/{org}/agents` does). S30-06: `?project=` narrows to that project and asks the same right
    on it.
    """
    from ..rbac import Permission

    org_wide = principal.can(Permission.MEMBER_READ, org)
    readable_projects = {
        key.split("/", 1)[1]
        for key in principal.project_roles
        if key.startswith(f"{org}/") and principal.can(Permission.MEMBER_READ, org, key.split("/", 1)[1])
    }
    if project and not (org_wide or project in readable_projects):
        raise forbidden(f"reading the members of {org}/{project} requires being a member of it")
    if not org_wide and not readable_projects:
        raise forbidden(f"reading the members of {org} requires being a member of it")
    organization = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organization is None:
        raise not_found("Organisation", org)
    query = (
        select(Membership, User, Project)
        .join(User, Membership.user_id == User.id)
        .outerjoin(Project, Membership.project_id == Project.id)
        .where(Membership.org_id == organization.id)
    )
    if project:
        query = query.where(Project.slug == project)
    rows = (await session.execute(query)).all()
    return [
        MembershipDto(
            user_id=user.id,
            email=user.email,
            org=org,
            project_slug=row_project.slug if row_project else None,
            role=Role(membership.role),
        )
        for membership, user, row_project in rows
        if org_wide or (row_project is not None and row_project.slug in readable_projects)
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
        raise forbidden("managing members needs the project_owner or org_admin role")
    # A project role adds to the organisation role (rbac.py): `org_admin` on a project row would
    # read as a project-scoped administrator, which RBAC does not have (S30-06).
    if body.project_slug and body.role == Role.ORG_ADMIN:
        raise unprocessable("`org_admin` is a role of the organisation, not of a project")
    # L'e-mail est unique à l'échelle de l'INSTANCE : un utilisateur déjà membre d'une autre
    # organisation est invisible sous la RLS de `users`, et la route tentait un doublon.
    async with en_portee_de_plateforme(session):
        user = (await session.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user is None:
        user = User(email=body.email, display_name=body.email.split("@")[0])
        session.add(user)
        await session.flush()
    project = None
    if body.project_slug:
        # Dans CETTE organisation : un slug existe dans plusieurs, et chercher par slug seul
        # rattachait le membre au premier venu — trouvé par le test de franchissement.
        project = (
            await session.execute(
                select(Project).where(Project.slug == body.project_slug, Project.org_id == organization.id)
            )
        ).scalar_one_or_none()
        if project is None:
            raise not_found("Project", f"{org}/{body.project_slug}")
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
    await record(
        session,
        principal,
        "member.add",
        org_id=organization.id,
        target_type="user",
        target_id=user.id,
        role=str(body.role),
    )
    return MembershipDto(
        user_id=user.id, email=user.email, org=org, project_slug=body.project_slug, role=body.role
    )


@router.delete(
    "/orgs/{org}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="removeMember",
)
async def remove_member(
    org: str, user_id: str, session: Db, principal: Me, project: Annotated[str | None, Query()] = None
) -> Response:
    """Retire une appartenance — à l'organisation, ou au seul projet nommé.

    Le dernier `org_admin` ne se retire pas : une organisation sans administrateur ne se gère plus
    que par la base. Un administrateur qui se retire lui-même le peut, s'il en reste un autre.
    """
    from ..rbac import Permission

    organization = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organization is None:
        raise not_found("Organisation", org)
    if not principal.can(Permission.MEMBER_MANAGE, org, project):
        raise forbidden("managing members needs the project_owner or org_admin role")
    projet_id = None
    if project:
        projet = (
            await session.execute(
                select(Project).where(Project.slug == project, Project.org_id == organization.id)
            )
        ).scalar_one_or_none()
        if projet is None:
            raise not_found("Project", f"{org}/{project}")
        projet_id = projet.id
    appartenance = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.org_id == organization.id,
                Membership.project_id == projet_id if projet_id else Membership.project_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if appartenance is None:
        raise not_found("Membership", f"{user_id} in {org}" + (f"/{project}" if project else ""))
    if appartenance.role == str(Role.ORG_ADMIN) and projet_id is None:
        administrateurs = (
            await session.execute(
                select(func.count())
                .select_from(Membership)
                .where(
                    Membership.org_id == organization.id,
                    Membership.project_id.is_(None),
                    Membership.role == str(Role.ORG_ADMIN),
                )
            )
        ).scalar_one()
        if administrateurs <= 1:
            raise conflict(f"the last administrator of {org} cannot be removed: appoint another one first")
    await session.delete(appartenance)
    await record(
        session,
        principal,
        "member.remove",
        org_id=organization.id,
        target_type="user",
        target_id=user_id,
        project=project,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/audit", response_model=AuditPage, operation_id="listAudit")
async def list_audit(
    session: Db,
    principal: Me,
    page: Pagination,
    actor: Annotated[str | None, Query()] = None,
    target_type: Annotated[str | None, Query()] = None,
) -> AuditPage:
    from ..rbac import Permission

    # Les organisations où le principal a VRAIMENT le droit de lire l'audit — pas celles où il
    # est simplement membre. La RLS borne déjà la session à ses organisations, mais elle ne sait
    # pas distinguer « membre de b » de « autorisé à lire l'audit de b » : ce filtre-ci est plus
    # fin, et c'est lui qui manquait. Jusqu'au 2026-09-26 la requête n'avait aucun `where`.
    lisibles = [org for org in principal.org_roles if principal.can(Permission.AUDIT_READ, org)]
    if not lisibles:
        raise forbidden("reading the audit log is reserved for owners and administrators")

    # Volontairement SANS `is_platform_admin()` : ce prédicat rend vrai pour TOUT `org_admin`
    # de N'IMPORTE QUELLE organisation (`rbac.py:112`), donc s'en servir ici rouvrirait la fuite
    # pour le rôle le plus répandu. Les lignes de plateforme (`org_id IS NULL` : connexions,
    # jetons) ne sortent que si le droit couvre TOUTES les organisations de l'instance — ce qui
    # est le cas normal d'une installation à une seule organisation, et n'est jamais vrai chez
    # un locataire parmi d'autres.
    # Sous la RLS de `organizations` (0.11), ce compte vaut le nombre d'organisations LISIBLES, et
    # l'administrateur d'une organisation sur deux passerait pour couvrir l'instance. Sans effet :
    # la RLS d'`audit_log` ne montre les lignes `org_id IS NULL` qu'en portée `*`. Le filtre ci-dessous
    # reste utile sans RLS (SQLite, développement).
    total = (await session.execute(select(func.count()).select_from(Organization))).scalar_one()
    toute_l_instance = len(lisibles) >= int(total)

    query = select(AuditLog).order_by(AuditLog.ts.desc())
    ids = select(Organization.id).where(Organization.slug.in_(lisibles))
    portee = AuditLog.org_id.in_(ids)
    query = query.where(or_(portee, AuditLog.org_id.is_(None)) if toute_l_instance else portee)
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


@router.get("/orgs/{org}/network-activity", response_model=NetworkActivity, operation_id="getNetworkActivity")
async def get_network_activity(
    org: str,
    session: Db,
    principal: Me,
    since: Annotated[datetime | None, Query()] = None,
    decision: Annotated[Literal["allowed", "denied", "rate_limited", "unreachable"] | None, Query()] = None,
    project: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> NetworkActivity:
    """Which hosts the organisation's runs reached or were refused, by host, layer, decision and
    reason (ADR 0070). Reading it takes `audit:read` (the organisation's administrators and owners)."""
    from ..rbac import Permission

    if not principal.can(Permission.AUDIT_READ, org):
        raise forbidden("reading the network activity is reserved for owners and administrators")
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organisation is None:
        raise not_found("Organisation", org)
    floor = since or (utcnow() - timedelta(hours=24))
    query = (
        select(NetworkDecision, Project.slug)
        .join(Project, Project.id == NetworkDecision.project_id)
        .where(NetworkDecision.org_id == organisation.id, NetworkDecision.last_at >= floor)
        .order_by(NetworkDecision.last_at.desc())
        .limit(20_000)
    )
    if decision:
        query = query.where(NetworkDecision.decision == decision)
    if project:
        query = query.where(Project.slug == project)
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row, slug in (await session.execute(query)).all():
        key = (row.host, row.layer, row.decision, row.reason)
        group = groups.setdefault(
            key, {"count": 0, "runs": set(), "projects": set(), "agents": set(), "last_at": row.last_at}
        )
        group["count"] += row.count
        group["runs"].add(row.run_id or row.id)
        group["projects"].add(slug)
        if row.agent_slug:
            group["agents"].add(row.agent_slug)
        group["last_at"] = max(group["last_at"], row.last_at)
    ranked = sorted(groups.items(), key=lambda kv: kv[1]["last_at"], reverse=True)[:limit]
    return NetworkActivity(
        rows=[
            NetworkActivityRow(
                host=host,
                layer=layer,
                decision=decided,
                reason=reason,
                count=int(g["count"]),
                runs=len(g["runs"]),
                projects=sorted(g["projects"]),
                agents=sorted(g["agents"]),
                last_at=g["last_at"],
            )
            for (host, layer, decided, reason), g in ranked
        ]
    )
