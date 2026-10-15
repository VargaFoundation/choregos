# SPDX-License-Identifier: Apache-2.0
"""An organisation's egress allowlist: the rules, the requests, the checks (S39-22, ADR 0066).

The list is decided in the app by the organisation's administrator (`tools:grant`), enforced by the
platform's own outbound clients, and bounded by the deployment's ceiling. The app cannot change the
cluster's network policy: what the platform must still open is shown, never applied."""

from __future__ import annotations

from typing import Annotated

from choregos_core import utcnow
from choregos_core.egress import FAMILIES, normalize_host
from fastapi import APIRouter, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..config import Settings
from ..db.models import Organization, OrgEgressRule
from ..deps import Config, Db, Me
from ..errors import ApiError, conflict, forbidden, not_found, unprocessable
from ..rbac import Permission
from ..schemas.egress import (
    EgressCheck,
    EgressCheckResult,
    EgressDecision,
    EgressOverview,
    EgressRequestCreate,
    EgressRule,
    EgressRuleCreate,
)
from ..services import egress as service
from ..services.egress import Target

router = APIRouter(tags=["egress"])

RuleId = Annotated[str, Path(min_length=1, max_length=64)]


async def _organisation(session: AsyncSession, slug: str) -> Organization:
    row = (await session.execute(select(Organization).where(Organization.slug == slug))).scalar_one_or_none()
    if row is None:
        raise not_found("Organisation", slug)
    return row


def _read(principal: Me, org: str) -> None:
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden(f"the network access of {org} is readable by its members only")


def _decide(principal: Me, org: str) -> None:
    if not principal.can(Permission.TOOLS_GRANT, org):
        raise forbidden(f"the network access of {org} is decided by its administrator")


def _host(raw: str) -> str:
    try:
        return normalize_host(raw, allow_wildcard=True)
    except ValueError as error:
        raise unprocessable(str(error)) from error


async def _live(session: AsyncSession, org_id: str, host: str) -> OrgEgressRule | None:
    return (
        await session.execute(
            select(OrgEgressRule).where(
                OrgEgressRule.org_id == org_id,
                OrgEgressRule.host == host,
                OrgEgressRule.status.in_(service.LIVE),
            )
        )
    ).scalar_one_or_none()


async def _rule(session: AsyncSession, organisation: Organization, rule_id: str) -> OrgEgressRule:
    row = await session.get(OrgEgressRule, rule_id)
    if row is None or row.org_id != organisation.id:
        raise not_found("Egress rule", rule_id)
    return row


@router.get("/orgs/{org}/egress", response_model=EgressOverview, operation_id="getOrgEgress")
async def get_org_egress(org: str, session: Db, principal: Me, settings: Config) -> EgressOverview:
    """The rules (live and decided), the pending requests, the deployment's ceiling and its mode."""
    _read(principal, org)
    organisation = await _organisation(session, org)
    rows = (
        await session.execute(
            select(OrgEgressRule)
            .where(OrgEgressRule.org_id == organisation.id)
            .order_by(OrgEgressRule.host, OrgEgressRule.created_at)
        )
    ).scalars()
    rules: list[EgressRule] = []
    requests: list[EgressRule] = []
    for row in rows:
        (requests if row.status == "requested" else rules).append(service.rule_dto(row))
    return EgressOverview(
        mode=service.mode_of(settings),
        ceiling={family: list(patterns) for family, patterns in service.ceiling_of(settings).items()},
        rules=rules,
        requests=requests,
        can_decide=principal.can(Permission.TOOLS_GRANT, org),
    )


def _targets_of(body: EgressCheck, settings: Settings) -> list[Target]:
    targets: list[Target] = []
    for item in body.targets:
        if item.url:
            found = service.targets_of_urls([item.url], item.used_by[0])
            if not found:
                raise unprocessable(f"`{item.url}` is not an http(s) URL")
            host, port = found[0].host, found[0].port
        elif item.host:
            host, port = _host(item.host), item.port or 443
        else:
            raise unprocessable("a target needs a `url` or a `host`")
        targets.extend(Target(host, port, family) for family in item.used_by)
    origin = body.origin
    if origin is not None:
        if origin.kind == "org-connector":
            from choregos_adapters import connector_types

            kinds = sorted({k for k, t, _ in connector_types() if t == origin.type})
            if len(kinds) != 1:
                raise unprocessable(f"`{origin.type}`: not a known connector type")
            targets.extend(service.targets_of_connector(kinds[0], origin.type or "", dict(origin.config)))
        elif origin.kind == "skill-import":
            targets.extend(service.skill_import_targets(origin.repo_url or ""))
        elif origin.kind == "knowledge-source":
            targets.extend(service.knowledge_source_targets(origin.type or "", dict(origin.config)))
        else:
            raise unprocessable(f"the origin `{origin.kind}` is not available on this deployment")
    unique: list[Target] = []
    for target in targets:
        if target not in unique:
            unique.append(target)
    if not unique:
        raise unprocessable("nothing to check: give targets or an origin")
    return unique


@router.post("/orgs/{org}/egress/check", response_model=EgressCheckResult, operation_id="checkOrgEgress")
async def check_org_egress(
    org: str, body: EgressCheck, session: Db, principal: Me, settings: Config
) -> EgressCheckResult:
    """Where each host stands: in this organisation's list, and against the deployment's ceiling.
    The hosts come from the targets, or are DERIVED by the server from an `origin`: the console
    never asks a person for one."""
    _read(principal, org)
    organisation = await _organisation(session, org)
    origin_text = body.origin.kind if body.origin else "manual"
    items = await service.verdicts(
        session,
        organisation,
        principal,
        _targets_of(body, settings),
        settings=settings,
        reason=body.reason,
        origin=origin_text,
    )
    return EgressCheckResult(
        verdicts=items, ok=all(v.org == "allowed" and v.ceiling != "outside" for v in items)
    )


@router.post(
    "/orgs/{org}/egress/rules",
    response_model=EgressRule,
    status_code=status.HTTP_201_CREATED,
    operation_id="createOrgEgressRule",
)
async def create_org_egress_rule(org: str, body: EgressRuleCreate, session: Db, principal: Me) -> EgressRule:
    """Open a host: the administrator alone. A host that already has a live rule is a 409."""
    _decide(principal, org)
    organisation = await _organisation(session, org)
    host = _host(body.host)
    if await _live(session, organisation.id, host) is not None:
        raise conflict(f"`{host}` already has a live rule in {org}: revoke or approve it")
    now = utcnow()
    row = OrgEgressRule(
        org_id=organisation.id,
        host=host,
        ports=sorted(set(body.ports)),
        used_by=[f for f in FAMILIES if f in body.used_by],
        status="allowed",
        reason=body.reason,
        origin=body.origin,
        requested_by=principal.email,
        requested_at=now,
        decided_by=principal.email,
        decided_at=now,
    )
    session.add(row)
    await session.flush()
    await record(
        session,
        principal,
        "egress.rule.create",
        org_id=organisation.id,
        target_type="egress_rule",
        target_id=row.id,
        host=host,
        ports=row.ports,
        used_by=row.used_by,
        origin=row.origin,
        reason=row.reason,
    )
    return service.rule_dto(row)


@router.delete(
    "/orgs/{org}/egress/rules/{rule_id}", response_model=EgressRule, operation_id="revokeOrgEgressRule"
)
async def revoke_org_egress_rule(org: str, rule_id: RuleId, session: Db, principal: Me) -> EgressRule:
    """Close a host. The platform's clients refuse it from the next call."""
    _decide(principal, org)
    row = await _rule(session, await _organisation(session, org), rule_id)
    if row.status != "allowed":
        raise conflict(f"the rule is {row.status}: only an allowed rule is revoked")
    row.status = "revoked"
    row.decided_by = principal.email
    row.decided_at = utcnow()
    await session.flush()
    await record(
        session,
        principal,
        "egress.rule.revoke",
        org_id=row.org_id,
        target_type="egress_rule",
        target_id=row.id,
        host=row.host,
        ports=row.ports,
        origin=row.origin,
        reason=row.reason,
    )
    return service.rule_dto(row)


@router.post(
    "/orgs/{org}/egress/requests",
    response_model=EgressRule,
    status_code=status.HTTP_201_CREATED,
    operation_id="requestOrgEgress",
)
async def request_org_egress(org: str, body: EgressRequestCreate, session: Db, principal: Me) -> EgressRule:
    """A member asks an administrator to open a host. It shows in the inbox of those who decide;
    asking twice for the same host answers with the request already waiting."""
    _read(principal, org)
    organisation = await _organisation(session, org)
    host = _host(body.host)
    live = await _live(session, organisation.id, host)
    if (
        live is not None
        and live.status == "allowed"
        and set(body.ports) <= set(live.ports)
        and set(body.used_by) <= set(live.used_by)
    ):
        raise conflict(f"`{host}` is already allowed in {org}")
    if live is not None and live.status == "requested":
        service._merge(live, body.ports, body.used_by)
        await session.flush()
        return service.rule_dto(live)
    if (
        await service.count_open_requests(session, organisation.id, principal.email)
        >= service.MAX_OPEN_REQUESTS
    ):
        raise ApiError(
            429,
            "Too many requests",
            f"you already have {service.MAX_OPEN_REQUESTS} requests waiting: wait for a decision",
        )
    if live is not None:
        # allowed, but for other ports or families: widening it is a decision, not a request
        raise conflict(
            f"`{host}` is allowed for other ports or families: an administrator widens it in /admin/egress"
        )
    row = OrgEgressRule(
        org_id=organisation.id,
        host=host,
        ports=sorted(set(body.ports)),
        used_by=[f for f in FAMILIES if f in body.used_by],
        status="requested",
        reason=body.reason,
        origin=body.origin,
        requested_by=principal.email,
        requested_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    await record(
        session,
        principal,
        "egress.request.create",
        org_id=organisation.id,
        target_type="egress_rule",
        target_id=row.id,
        host=host,
        ports=row.ports,
        used_by=row.used_by,
        origin=row.origin,
        reason=row.reason,
    )
    return service.rule_dto(row)


async def _decision(
    org: str, rule_id: str, body: EgressDecision, session: AsyncSession, principal: Me, *, approve: bool
) -> EgressRule:
    _decide(principal, org)
    row = await _rule(session, await _organisation(session, org), rule_id)
    if row.status != "requested":
        raise conflict(f"the request is already {row.status}")
    row.status = "allowed" if approve else "refused"
    row.decided_by = principal.email
    row.decided_at = utcnow()
    row.decision_note = body.note
    await session.flush()
    await record(
        session,
        principal,
        "egress.request.approve" if approve else "egress.request.refuse",
        org_id=row.org_id,
        target_type="egress_rule",
        target_id=row.id,
        host=row.host,
        ports=row.ports,
        origin=row.origin,
        reason=row.reason,
        note=body.note,
    )
    return service.rule_dto(row)


@router.post(
    "/orgs/{org}/egress/requests/{rule_id}/approve",
    response_model=EgressRule,
    operation_id="approveOrgEgressRequest",
)
async def approve_org_egress_request(
    org: str, rule_id: RuleId, session: Db, principal: Me, body: EgressDecision | None = None
) -> EgressRule:
    return await _decision(org, rule_id, body or EgressDecision(), session, principal, approve=True)


@router.post(
    "/orgs/{org}/egress/requests/{rule_id}/refuse",
    response_model=EgressRule,
    operation_id="refuseOrgEgressRequest",
)
async def refuse_org_egress_request(
    org: str, rule_id: RuleId, session: Db, principal: Me, body: EgressDecision | None = None
) -> EgressRule:
    return await _decision(org, rule_id, body or EgressDecision(), session, principal, approve=False)
