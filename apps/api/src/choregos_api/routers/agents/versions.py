# SPDX-License-Identifier: Apache-2.0
"""Publishing, restoring and reading the immutable versions of an agent."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...audit import record
from ...db.models import (
    Agent,
    AgentVersion,
    Organization,
)
from ...deps import Db, Me
from ...errors import conflict, not_found, unprocessable
from ...rbac import Permission
from ...schemas import (
    AgentSpec,
    AgentVersionDto,
    AgentVersionPublish,
    AgentVersionRestore,
)
from ...schemas.agents import AgentAccess, check_remote_kind
from ...services.agent_access import (
    enforce_decision,
    load_visible_agent,
    project_in_org,
)
from ...services.agent_evals import gate
from ...services.agent_governance import (
    Action,
    Subject,
    decide,
)
from ...services.agent_reach import access_of, version_of_project
from ...services.agent_releases import (
    after_publish,
)
from ...services.agent_views import agent_pins, agent_version_dto, agent_versions
from ...services.agents import (
    digest,
    version_errors,
)
from ...services.egress import egress_gate_for_remote_agent
from ...services.hosted_builds import builds_for
from ...services.hosted_images import pin_hosted_runtime
from ...services.memory_access import check_bindings_at_publish
from ...services.network import check_agent_network
from ...services.remote_agents import pin_remote_runtime
from ._common import _model_allowed, _skills_usable
from ._router import Slug, router


async def _append_version(  # noqa: PLR0913 - the actors and the three ways a version is made
    session: AsyncSession,
    principal: Me,
    org: str,
    organisation: Organization,
    agent: Agent,
    subject: Subject,
    spec: AgentSpec,
    *,
    message: str | None,
    restored_from: AgentVersion | None = None,
    base_version: int | None = None,
    allow_egress: bool = False,
) -> AgentVersion:
    """The one way a version is appended (S40-12, ADR 0076): who may publish, the spec's own checks,
    the eval gate — and, when `base_version` is given, the two refusals that make a publication
    deliberate: the latest moved since the author read it (`stale_base`), or nothing changed
    (`no_change`). A restore carries the source's exact spec and checksum. Nothing is ever rewritten.
    A remote agent's card host is opened first (S39-22, ADR 0066), with `allow_egress`."""
    enforce_decision(decide(principal, org, subject, Action.PUBLISH))
    version_errors(spec)
    await check_agent_network(session, organisation.id, spec)
    await _skills_usable(session, organisation, spec)
    await _model_allowed(session, organisation, spec)
    try:
        check_remote_kind(agent.kind, spec)
    except ValueError as erreur:
        raise unprocessable(str(erreur)) from erreur
    await check_bindings_at_publish(session, principal, organisation, spec, project_slug=subject.project_slug)
    await egress_gate_for_remote_agent(session, principal, organisation, spec, allow_egress)
    spec = await pin_remote_runtime(session, organisation.id, spec)
    if restored_from is None:
        # A restore keeps the digest its version was published with: the stored bytes, not the tag now.
        spec = await pin_hosted_runtime(session, organisation.id, spec)
    if agent.status == "revoked":
        raise conflict(f"agent `{agent.slug}` is revoked: it can no longer publish")
    if agent.lifecycle == "retired":
        raise conflict(f"agent `{agent.slug}` is retired: it can no longer publish")
    versions = await agent_versions(session, agent)
    latest_row = versions[0] if versions else None
    version_number = latest_row.version if latest_row else 0
    checksum = restored_from.checksum if restored_from is not None else digest(spec)
    if base_version is not None:
        if base_version != version_number:
            author = latest_row.created_by if latest_row and latest_row.created_by else "someone"
            raise conflict(
                f"v{version_number} of `{agent.slug}` was published by {author} "
                f"since you started from v{base_version}",
                extra={
                    "code": "stale_base",
                    "latest_version": version_number,
                    "latest_author": latest_row.created_by if latest_row else None,
                },
            )
        if latest_row is not None and checksum == latest_row.checksum:
            raise conflict(
                f"nothing changed since v{version_number}",
                extra={"code": "no_change", "latest_version": version_number},
            )
    # The published version's eval suite, and the new one's, gate the publication (S32-03).
    gate(spec, AgentSpec.model_validate(latest_row.spec) if latest_row else None)
    db_row = AgentVersion(
        agent_id=agent.id,
        org_id=organisation.id,
        version=version_number + 1,
        spec=restored_from.spec if restored_from is not None else spec.model_dump(mode="json"),
        checksum=checksum,
        created_by=principal.email,
        message=message,
        restored_from=restored_from.version if restored_from is not None else None,
    )
    session.add(db_row)
    await session.flush()
    await record(
        session,
        principal,
        "agent.publish",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        version=db_row.version,
        **({"restored_from": db_row.restored_from} if db_row.restored_from else {}),
        **({"message_chars": len(message)} if message else {}),
    )
    # What saving a revision does to the channels is the agent's policy (ADR 0071).
    await after_publish(session, principal, agent, db_row.version, principal.email)
    return db_row


@router.post(
    "/orgs/{org}/agents/{slug}/versions",
    response_model=AgentVersionDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="publishAgentVersion",
)
async def publish_version(
    org: str, slug: Slug, body: AgentSpec, session: Db, principal: Me, allow_egress: bool = False
) -> AgentVersionDto:
    """The next version; none is ever rewritten. Superseded by `publishAgentVersionWithMessage`
    (a message, a base): kept as is for the clients written before, which publish with no message."""
    organisation, agent, subject = await load_visible_agent(session, principal, org, slug)
    db_row = await _append_version(
        session, principal, org, organisation, agent, subject, body, message=None, allow_egress=allow_egress
    )
    return agent_version_dto(db_row, agent, builds=await builds_for(session, agent.org_id, [db_row.spec]))


@router.post(
    "/orgs/{org}/agents/{slug}/versions/publish",
    response_model=AgentVersionDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="publishAgentVersionWithMessage",
)
async def publish_version_with_message(
    org: str, slug: Slug, body: AgentVersionPublish, session: Db, principal: Me, allow_egress: bool = False
) -> AgentVersionDto:
    """The next version, with the author's message, from the version they read: 409 `stale_base` when
    the latest moved meanwhile, 409 `no_change` when the spec equals the latest's (S40-12)."""
    organisation, agent, subject = await load_visible_agent(session, principal, org, slug)
    db_row = await _append_version(
        session,
        principal,
        org,
        organisation,
        agent,
        subject,
        body.spec,
        message=body.message,
        base_version=body.base_version,
        allow_egress=allow_egress,
    )
    return agent_version_dto(db_row, agent, builds=await builds_for(session, agent.org_id, [db_row.spec]))


@router.post(
    "/orgs/{org}/agents/{slug}/versions/{version}/restore",
    response_model=AgentVersionDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="restoreAgentVersion",
)
async def restore_version(
    org: str, slug: Slug, version: int, body: AgentVersionRestore, session: Db, principal: Me
) -> AgentVersionDto:
    """Copy version N, byte for byte (same spec, same checksum), as the next version. It is a new
    version that remembers where it came from; nothing is rewritten or deleted (S40-12)."""
    organisation, agent, subject = await load_visible_agent(session, principal, org, slug)
    source = (
        await session.execute(
            select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.version == version)
        )
    ).scalar_one_or_none()
    if source is None:
        raise not_found("Agent version", f"{slug}@{version}")
    db_row = await _append_version(
        session,
        principal,
        org,
        organisation,
        agent,
        subject,
        AgentSpec.model_validate(source.spec),
        message=body.message or f"Restore v{version}",
        restored_from=source,
        base_version=body.base_version,
    )
    return agent_version_dto(db_row, agent, builds=await builds_for(session, agent.org_id, [db_row.spec]))


@router.get(
    "/orgs/{org}/agents/{slug}/versions/{version}",
    response_model=AgentVersionDto,
    operation_id="getAgentVersion",
)
async def get_version(org: str, slug: Slug, version: int, session: Db, principal: Me) -> AgentVersionDto:
    _, agent, _ = await load_visible_agent(session, principal, org, slug)
    db_row = (
        await session.execute(
            select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.version == version)
        )
    ).scalar_one_or_none()
    if db_row is None:
        raise not_found("Agent version", f"{slug}@{version}")
    return agent_version_dto(
        db_row,
        agent,
        await agent_pins(session, principal, org, agent),
        await builds_for(session, agent.org_id, [db_row.spec]),
    )


@router.get(
    "/orgs/{org}/agents/{slug}/access",
    response_model=AgentAccess,
    operation_id="getAgentAccess",
)
async def get_access(
    org: str,
    slug: Slug,
    session: Db,
    principal: Me,
    project: Annotated[str, Query(min_length=1, max_length=200)],
    version: Annotated[int | None, Query(ge=1)] = None,
) -> AgentAccess:
    """What one version of the agent reaches in one project, each thing with its origin (ADR 0127).

    The version is the one given, else the one a run of the project takes (pin, channel, production).
    For whoever may read the agent AND the project; anyone else gets the 404 of the sibling routes."""
    organisation, agent, _ = await load_visible_agent(session, principal, org, slug)
    row = await project_in_org(session, organisation, project)
    if not principal.can(Permission.PROJECT_READ, org, row.slug):
        raise not_found("Project", f"{org}/{project}")
    number = version if version is not None else await version_of_project(session, agent, row)
    stored = (
        await session.execute(
            select(AgentVersion.spec).where(AgentVersion.agent_id == agent.id, AgentVersion.version == number)
        )
    ).scalar_one_or_none()
    if stored is None:
        raise not_found("Agent version", f"{slug}@{number}")
    return await access_of(session, agent, number, AgentSpec.model_validate(stored), row)
