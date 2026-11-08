# SPDX-License-Identifier: Apache-2.0
"""Helpers every knowledge route group shares: gates, grant loading, the DTO builders."""

from __future__ import annotations

import re
from weakref import WeakKeyDictionary

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import (
    Agent,
    AgentVersion,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeGrant,
    KnowledgeSource,
    Membership,
    Organization,
    Project,
    User,
)
from ...deps import Me
from ...errors import forbidden, not_found, unprocessable
from ...knowledge.access import (
    ROLES,
    KnowledgeAction,
    can_manage,
    decide,
    role_of,
)
from ...knowledge.index import search_mode
from ...schemas.knowledge import (
    KnowledgeBaseDto,
    KnowledgeBaseUser,
    KnowledgeDocumentDto,
    KnowledgeGrantDto,
    KnowledgePermissions,
    KnowledgeSourceDto,
)
from ...services.agent_governance import visible_clause
from ...services.organisations import organisation_by_slug


def _member(principal: Me, org: str) -> None:
    if principal.org_roles.get(org) is None and not any(
        key.startswith(f"{org}/") for key in principal.project_roles
    ):
        raise forbidden(f"reading the knowledge bases of {org} requires being a member of it")


def _manage(principal: Me, org: str) -> None:
    if not can_manage(principal, org):
        raise forbidden("curating a knowledge base needs the org_admin role")


#: The grants of the bases loaded in this request, so `_require` never queries twice for one base.
_GRANTS: WeakKeyDictionary[KnowledgeBase, list[KnowledgeGrant]] = WeakKeyDictionary()


def _require(principal: Me, org: str, base: KnowledgeBase, action: KnowledgeAction) -> None:
    """The one gate (ADR 0081): the server's own sentence when the caller may not."""
    decision = decide(principal, org, base, action, _GRANTS.get(base, []))
    if not decision.allowed:
        raise forbidden(decision.reason or "not allowed on this knowledge base")


async def _grants_of(session: AsyncSession, base_ids: list[str]) -> dict[str, list[KnowledgeGrant]]:
    """The grants of these bases, in one query."""
    found: dict[str, list[KnowledgeGrant]] = {base_id: [] for base_id in base_ids}
    if not base_ids:
        return found
    rows = (
        await session.execute(
            select(KnowledgeGrant)
            .where(KnowledgeGrant.knowledge_base_id.in_(base_ids))
            .order_by(KnowledgeGrant.subject_type, KnowledgeGrant.subject)
        )
    ).scalars()
    for grant in rows:
        found[grant.knowledge_base_id].append(grant)
    return found


async def _load_grants(session: AsyncSession, base: KnowledgeBase) -> list[KnowledgeGrant]:
    if base not in _GRANTS:
        _GRANTS[base] = (await _grants_of(session, [base.id]))[base.id]
    return _GRANTS[base]


async def _base(session: AsyncSession, principal: Me, org: str, slug: str) -> KnowledgeBase:
    """The base, if the caller may read it; one it may not read does not exist for it (404).

    Reading is the reach, or any role granted on the base (ADR 0081), so the grants are loaded here.
    """
    organisation = await organisation_by_slug(session, org)
    base = (
        await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.org_id == organisation.id, KnowledgeBase.slug == slug)
        )
    ).scalar_one_or_none()
    if base is None:
        raise not_found("Knowledge base", f"{org}/{slug}")
    grants = await _load_grants(session, base)
    if not decide(principal, org, base, KnowledgeAction.READ, grants).allowed:
        raise not_found("Knowledge base", f"{org}/{slug}")
    return base


async def _known_projects(session: AsyncSession, organisation: Organization, slugs: list[str]) -> None:
    if not slugs:
        return
    known = set(
        (
            await session.execute(
                select(Project.slug).where(Project.org_id == organisation.id, Project.slug.in_(slugs))
            )
        ).scalars()
    )
    if unknown := sorted(set(slugs) - known):
        raise unprocessable(f"unknown project(s) in {organisation.slug}: {', '.join(unknown)}")


def _document_dto(document: KnowledgeDocument, *, with_markdown: bool = False) -> KnowledgeDocumentDto:
    return KnowledgeDocumentDto(
        id=document.id,
        filename=document.filename,
        title=document.title,
        mime=document.mime,
        size_bytes=document.size_bytes,
        digest=document.digest,
        source=document.source,
        status=document.status,
        error=document.error,
        warnings=list(document.warnings or []),
        created_by=document.created_by,
        created_at=document.created_at,
        converted_at=document.converted_at,
        markdown=document.markdown if with_markdown else None,
        source_id=document.source_id,
        source_url=document.source_url,
    )


async def _dto(
    session: AsyncSession,
    principal: Me,
    org: str,
    base: KnowledgeBase,
    *,
    with_documents: bool = False,
    grants: list[KnowledgeGrant] | None = None,
) -> KnowledgeBaseDto:
    held = grants if grants is not None else await _load_grants(session, base)
    allowed = {action: decide(principal, org, base, action, held) for action in KnowledgeAction}
    can = KnowledgePermissions(
        edit=allowed[KnowledgeAction.EDIT].allowed,
        distill=allowed[KnowledgeAction.DISTILL].allowed,
        share=allowed[KnowledgeAction.SHARE].allowed,
        archive=allowed[KnowledgeAction.ARCHIVE].allowed,
        widen=allowed[KnowledgeAction.WIDEN].allowed,
    )
    shown = await _grant_dtos(session, base, held) if with_documents and can.share else None
    counts = {
        str(state): int(count)
        for state, count in (
            await session.execute(
                select(KnowledgeDocument.status, func.count())
                .where(KnowledgeDocument.knowledge_base_id == base.id)
                .group_by(KnowledgeDocument.status)
            )
        ).tuples()
    }
    documents = None
    if with_documents:
        documents = [
            _document_dto(document)
            for document in (
                await session.execute(
                    select(KnowledgeDocument)
                    .where(KnowledgeDocument.knowledge_base_id == base.id)
                    .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
                )
            ).scalars()
        ]
    return KnowledgeBaseDto(
        slug=base.slug,
        name=base.name,
        description=base.description,
        scope=base.scope,
        projects=list(base.projects or []),
        groups=list(base.groups or []),
        serving_mode=base.serving_mode,
        status=base.status,
        document_count=sum(counts.values()),
        documents_by_status=counts,
        created_by=base.created_by,
        created_at=base.created_at,
        updated_at=base.updated_at,
        documents=documents,
        index=await _index_state(session, base) if base.serving_mode == "index" else None,
        eval_set=list(base.eval_set or []),
        eval_result=base.eval_result,
        sources=[await _source_dto(session, source) for source in await _sources(session, base)]
        if with_documents
        else None,
        distillation=base.distillation,
        owner_project=base.owner_project,
        restricted=base.restricted,
        can=can,
        cannot_reason=allowed[KnowledgeAction.EDIT].reason,
        my_role=role_of(principal, org, base, held),
        grants=shown,
        used_by=await _used_by(session, principal, org, base) if with_documents else None,
    )


async def _used_by(
    session: AsyncSession, principal: Me, org: str, base: KnowledgeBase
) -> list[KnowledgeBaseUser]:
    """The agents the caller sees whose CURRENT revision names the base (`spec.knowledge`).

    Current is the revision a project is served by default: production (`stable_version`), else the
    latest (`agent_releases.served`). Only an agent that runs reads it: a draft does not run yet, a
    retired one never again (S32-04); a deprecated one still runs. One query over the organisation's
    agents; the spec is JSON, filtered here rather than in SQL (SQLite and PostgreSQL read JSON
    differently).
    """
    organisation = await organisation_by_slug(session, org)
    latest = (
        select(AgentVersion.agent_id, func.max(AgentVersion.version).label("version"))
        .where(AgentVersion.org_id == organisation.id)
        .group_by(AgentVersion.agent_id)
        .subquery()
    )
    current = func.coalesce(Agent.stable_version, latest.c.version)
    rows = (
        await session.execute(
            select(Agent.slug, Agent.display_name, AgentVersion.version, AgentVersion.spec)
            .join(latest, latest.c.agent_id == Agent.id)
            .join(AgentVersion, (AgentVersion.agent_id == Agent.id) & (AgentVersion.version == current))
            .where(
                Agent.org_id == organisation.id,
                Agent.lifecycle.in_(("active", "deprecated")),
                await visible_clause(session, principal, organisation),
            )
            .order_by(Agent.slug)
        )
    ).tuples()
    return [
        KnowledgeBaseUser(agent=slug, display_name=name, version=int(version))
        for slug, name, version, spec in rows
        if any(
            isinstance(ref, dict) and ref.get("slug") == base.slug
            for ref in (spec or {}).get("knowledge") or []
        )
    ]


async def _sources(session: AsyncSession, base: KnowledgeBase) -> list[KnowledgeSource]:
    return list(
        (
            await session.execute(
                select(KnowledgeSource)
                .where(KnowledgeSource.knowledge_base_id == base.id)
                .order_by(KnowledgeSource.created_at, KnowledgeSource.id)
            )
        ).scalars()
    )


async def _source_dto(session: AsyncSession, source: KnowledgeSource) -> KnowledgeSourceDto:
    count = (
        await session.execute(select(func.count()).where(KnowledgeDocument.source_id == source.id))
    ).scalar_one()
    return KnowledgeSourceDto(
        id=source.id,
        kind=source.kind,
        name=source.name,
        connector=source.connector,
        config=dict(source.config or {}),
        interval_minutes=source.interval_minutes,
        refresh_mode=source.refresh_mode,
        refresh_cron=source.refresh_cron,
        refresh_timezone=source.refresh_timezone,
        next_sync_at=source.next_sync_at,
        sync_requested_at=source.sync_requested_at,
        status=source.status,
        last_synced_at=source.last_synced_at,
        last_error=source.last_error,
        last_summary=source.last_summary,
        document_count=int(count),
    )


async def _index_state(session: AsyncSession, base: KnowledgeBase) -> dict[str, object]:
    chunks, documents = (
        await session.execute(
            select(
                func.count(KnowledgeChunk.id), func.count(func.distinct(KnowledgeChunk.document_id))
            ).where(KnowledgeChunk.knowledge_base_id == base.id)
        )
    ).one()
    return {"chunks": int(chunks), "documents": int(documents), "mode": await search_mode(session)}


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def _members_of(session: AsyncSession, organisation: Organization) -> list[User]:
    """The people who belong to the organisation, at any level."""
    return list(
        (
            await session.execute(
                select(User)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.org_id == organisation.id)
                .distinct()
            )
        ).scalars()
    )


async def _grant_dtos(
    session: AsyncSession, base: KnowledgeBase, grants: list[KnowledgeGrant]
) -> list[KnowledgeGrantDto]:
    """The grants as shown: a name for each user, and which e-mails still wait for a person."""
    organisation = await session.get(Organization, base.org_id)
    members = await _members_of(session, organisation) if organisation is not None else []
    by_id = {member.id: member for member in members}
    by_email = {member.email.lower(): member for member in members}
    shown: list[KnowledgeGrantDto] = []
    for grant in grants:
        display: str | None = grant.subject
        pending = False
        if grant.subject_type == "user":
            member = by_id.get(grant.subject) or by_email.get(grant.subject.lower())
            pending = member is None
            display = (member.display_name or member.email) if member is not None else grant.subject
        shown.append(
            KnowledgeGrantDto(
                subject_type=grant.subject_type,
                subject=grant.subject,
                display=display,
                role=grant.role,
                pending=pending,
                created_by=grant.created_by,
                created_at=grant.created_at,
            )
        )
    return shown


async def _checked_grants(
    session: AsyncSession, organisation: Organization, wanted: list[KnowledgeGrantDto]
) -> dict[tuple[str, str], str]:
    """The (type, subject) -> role map to store, or a 422 saying what is wrong.

    A user is a member id, or an e-mail: the id of the member that owns it when there is one, else the
    e-mail as typed in lower case, which matches that person the day they sign in (D11-4). A project
    must exist; a group is a free IdP name.
    """
    members = await _members_of(session, organisation)
    by_id = {member.id: member for member in members}
    by_email = {member.email.lower(): member for member in members}
    known = set(
        (await session.execute(select(Project.slug).where(Project.org_id == organisation.id))).scalars()
    )
    stored: dict[tuple[str, str], str] = {}
    for item in wanted:
        subject = item.subject.strip()
        if item.subject_type == "user":
            if "@" in subject:
                if not _EMAIL.match(subject):
                    raise unprocessable(f"`{subject}` is not an e-mail address")
                member = by_email.get(subject.lower())
                subject = member.id if member is not None else subject.lower()
            elif subject not in by_id:
                raise unprocessable(f"unknown member in {organisation.slug}: {subject}")
        elif item.subject_type == "project":
            if subject not in known:
                raise unprocessable(f"unknown project(s) in {organisation.slug}: {subject}")
        elif not subject:
            raise unprocessable("a group grant names an IdP group")
        if item.role not in ROLES:
            raise unprocessable(f"unknown role `{item.role}`")
        if (item.subject_type, subject) in stored:
            raise unprocessable(f"{item.subject_type} `{subject}` is listed twice")
        stored[(item.subject_type, subject)] = item.role
    return stored
