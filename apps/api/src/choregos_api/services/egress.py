# SPDX-License-Identifier: Apache-2.0
"""The organisation's egress allowlist: loading it, checking it, and the gate every governed "add"
passes before its side effect (S39-22, ADR 0066)."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from choregos_contracts import Role
from choregos_core import utcnow
from choregos_core.egress import (
    FAMILIES,
    AllowedRule,
    EgressPolicy,
    Mode,
    Verdict,
    ceiling_from,
    default_mode,
    host_port_of,
    normalize_host,
    use_policy,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..config import Settings, get_settings
from ..db.models import Membership, Organization, OrgEgressRule, User
from ..errors import ApiError, forbidden
from ..rbac import Permission, Principal
from ..schemas.egress import (
    Approver,
    EgressRequired,
    EgressRule,
    EgressVerdict,
    PlatformChange,
)

LIVE = ("requested", "allowed")
#: A member may have this many requests waiting at once.
MAX_OPEN_REQUESTS = 20

#: The pod selector of each family in the chart, when the deployment does not say otherwise.
DEFAULT_SELECTORS = {
    "api": "app.kubernetes.io/name=choregos-api",
    "workers": "app.kubernetes.io/name=choregos-orchestrator",
    "runners": "app.kubernetes.io/name=choregos-runner",
}
DEFAULT_TEMPLATE = """\
# Allow egress from the Choregos {{family}} pods ({{selector}}) to:
toFQDNs:
{{#fqdns}}
  - {{.}}
{{/fqdns}}
toPorts: {{ports}}
# Reason: {{reason}} (requested by {{requested_by}}, origin {{origin}})
"""


@dataclass(frozen=True, slots=True)
class Target:
    host: str
    port: int = 443
    family: str = "api"


def mode_of(settings: Settings) -> Mode:
    return default_mode(settings.egress_mode or None)


def ceiling_of(settings: Settings) -> dict[str, tuple[str, ...]]:
    """The deployment's ceiling. `CHOREGOS_API_EGRESS_HOSTS` (the catalogue's older declaration) is
    read as `{"api": <hosts>}` when the ceiling does not name the API family: deprecated, one minor
    version."""
    declared = dict(settings.egress_ceiling)
    if settings.api_egress_hosts and not declared.get("api"):
        declared["api"] = list(settings.api_egress_hosts)
    return ceiling_from(declared)


def to_rule(row: OrgEgressRule) -> AllowedRule:
    return AllowedRule(row.host, tuple(row.ports or (443,)), tuple(row.used_by or ("api",)))


async def load_policy(
    session: AsyncSession, organisation: Organization, settings: Settings | None = None
) -> EgressPolicy:
    settings = settings or get_settings()
    rows = (
        await session.execute(
            select(OrgEgressRule).where(
                OrgEgressRule.org_id == organisation.id, OrgEgressRule.status.in_(LIVE)
            )
        )
    ).scalars()
    allowed: list[AllowedRule] = []
    requested: list[AllowedRule] = []
    for row in rows:
        (allowed if row.status == "allowed" else requested).append(to_rule(row))
    return EgressPolicy(
        org=organisation.slug,
        rules=tuple(allowed),
        requested=tuple(requested),
        ceiling=ceiling_of(settings),
        mode=mode_of(settings),
    )


@asynccontextmanager
async def egress_scope(
    session: AsyncSession,
    organisation: Organization,
    family: str = "api",
    origin: str = "",
    settings: Settings | None = None,
) -> AsyncIterator[None]:
    """Every guarded outbound call made inside the block is checked against the organisation's list."""
    policy = await load_policy(session, organisation, settings)
    with use_policy(policy, family, origin):
        yield


async def organisation_by_id(session: AsyncSession, org_id: str) -> Organization | None:
    return await session.get(Organization, org_id)


async def approvers(session: AsyncSession, org_id: str) -> list[Approver]:
    rows = (
        await session.execute(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.org_id == org_id,
                Membership.project_id.is_(None),
                Membership.role == Role.ORG_ADMIN.value,
                User.is_service.is_(False),
            )
            .order_by(User.email)
        )
    ).scalars()
    return [Approver(display_name=u.display_name or u.email, email=u.email) for u in rows]


# ───────────────────────────── the platform change, as text ─────────────────────────────

_SECTION = re.compile(r"{{#(\w+)}}\n?(.*?){{/\1}}\n?", re.S)


def render(template: str, variables: dict[str, Any], *, encode: bool = False) -> str:
    """A tiny mustache: `{{name}}`, `{{.}}` and `{{#list}}…{{/list}}` over a list of strings. A
    section tag alone on its line leaves no blank line behind."""

    def scalar(value: Any) -> str:
        text = ", ".join(map(str, value)) if isinstance(value, list | tuple) else str(value)
        return quote(text, safe="") if encode else text

    def section(match: re.Match[str]) -> str:
        items = variables.get(match.group(1)) or []
        return "".join(re.sub(r"{{\.}}", lambda _m, i=item: scalar(i), match.group(2)) for item in items)

    # a standalone section tag: drop the whole line, keep the body
    template = re.sub(r"^[ \t]*({{[#/]\w+}})[ \t]*\n", r"\1", template, flags=re.M)
    out = _SECTION.sub(section, template)
    return re.sub(
        r"{{(\w+)}}", lambda m: scalar(variables[m.group(1)]) if m.group(1) in variables else "", out
    )


def platform_change(
    verdict: Verdict,
    settings: Settings,
    *,
    reason: str = "",
    origin: str = "",
    requested_by: str = "",
) -> PlatformChange:
    """What the platform must still do for this host. The CE ships a neutral default; a deployment
    brings its own template (`CHOREGOS_EGRESS_PLATFORM_CHANGE`)."""
    config = settings.egress_platform_change or {}
    needed = verdict.ceiling != "open"
    if not needed:
        return PlatformChange(needed=False, format=config.get("format", "text"))
    entries = config.get("entries") or {}
    selectors = {**DEFAULT_SELECTORS, **(config.get("selectors") or {})}
    host = verdict.host
    fqdn = f'matchPattern: "{host}"' if host.startswith("*.") else f'matchName: "{host}"'
    variables: dict[str, Any] = {
        "family": verdict.family,
        "selector": selectors.get(verdict.family, ""),
        "entry": entries.get(verdict.family, verdict.family),
        "hosts": [host],
        "hosts_csv": host,
        "fqdns": [fqdn],
        "ports": f"[{verdict.port}]",
        "reason": reason or "no reason given",
        "origin": origin or "manual",
        "requested_by": requested_by or "an administrator",
    }
    snippet = render(config.get("template") or DEFAULT_TEMPLATE, variables).rstrip("\n") + "\n"
    issue_template = config.get("issueUrl")
    issue_url = None
    if issue_template:
        issue_url = render(str(issue_template), {**variables, "snippet_urlencoded": snippet}, encode=True)
        # `snippet_urlencoded` is already the snippet, encoded once by `render`
    return PlatformChange(
        needed=True,
        format=config.get("format", "cilium-fqdn"),
        snippet=snippet,
        issue_url=issue_url,
        owner=config.get("owner"),
    )


# ───────────────────────────── verdicts and the gate ─────────────────────────────


def targets_of_urls(urls: Iterable[object], family: str = "api") -> list[Target]:
    seen: list[Target] = []
    for url in urls:
        hp = host_port_of(url)
        if hp and Target(hp[0], hp[1], family) not in seen:
            seen.append(Target(hp[0], hp[1], family))
    return seen


def targets_of_hosts(hosts: Iterable[tuple[str, int]], family: str = "api") -> list[Target]:
    out: list[Target] = []
    for host, port in hosts:
        target = Target(normalize_host(host), port, family)
        if target not in out:
            out.append(target)
    return out


def targets_of_connector(kind: str, type_name: str, config: dict[str, Any]) -> list[Target]:
    from choregos_adapters import egress_hosts

    hosts = egress_hosts(kind, type_name, config)
    # the broker, a test and a discovery call out from the API; a documents connector is also
    # synced by the workers
    families = ("api", "workers") if kind == "documents" else ("api",)
    return [t for family in families for t in targets_of_hosts(hosts, family)]


async def verdicts(
    session: AsyncSession,
    organisation: Organization,
    principal: Principal,
    targets: Iterable[Target],
    *,
    settings: Settings | None = None,
    reason: str = "",
    origin: str = "",
) -> list[EgressVerdict]:
    settings = settings or get_settings()
    policy = await load_policy(session, organisation, settings)
    can_allow = principal.can(Permission.TOOLS_GRANT, organisation.slug)
    people = await approvers(session, organisation.id)
    live = {
        row.host: row
        for row in (
            await session.execute(
                select(OrgEgressRule).where(
                    OrgEgressRule.org_id == organisation.id, OrgEgressRule.status == "requested"
                )
            )
        ).scalars()
    }
    out: list[EgressVerdict] = []
    for target in targets:
        verdict = policy.verdict(target.host, target.port, target.family)
        pending = live.get(verdict.host) if verdict.org == "requested" else None
        out.append(
            EgressVerdict(
                host=verdict.host,
                port=verdict.port,
                used_by=target.family,
                org=verdict.org,
                ceiling=verdict.ceiling,
                can_allow=can_allow,
                approvers=people,
                platform_change=platform_change(
                    verdict,
                    settings,
                    reason=reason,
                    origin=origin,
                    requested_by=principal.email,
                ),
                requested_by=pending.requested_by if pending else None,
                requested_at=pending.requested_at if pending else None,
            )
        )
    return out


def _merge(row: OrgEgressRule, ports: Iterable[int], used_by: Iterable[str]) -> None:
    row.ports = sorted({*(row.ports or []), *ports})
    row.used_by = [f for f in FAMILIES if f in {*(row.used_by or []), *used_by}]


async def allow_hosts(
    session: AsyncSession,
    principal: Principal,
    organisation: Organization,
    targets: Iterable[Target],
    *,
    origin: str,
    reason: str,
) -> list[OrgEgressRule]:
    """Open every target, in the caller's transaction: a live row is widened (and a `requested` one
    approved), anything else is created `allowed`. Audited."""
    by_host: dict[str, tuple[set[int], set[str]]] = {}
    for target in targets:
        ports, families = by_host.setdefault(normalize_host(target.host, allow_wildcard=True), (set(), set()))
        ports.add(target.port)
        families.add(target.family)
    now = utcnow()
    rules: list[OrgEgressRule] = []
    for host, (ports, families) in by_host.items():
        row = (
            await session.execute(
                select(OrgEgressRule).where(
                    OrgEgressRule.org_id == organisation.id,
                    OrgEgressRule.host == host,
                    OrgEgressRule.status.in_(LIVE),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = OrgEgressRule(
                org_id=organisation.id,
                host=host,
                ports=sorted(ports),
                used_by=[f for f in FAMILIES if f in families],
                status="allowed",
                reason=reason[:500],
                origin=origin[:200],
                requested_by=principal.email,
                requested_at=now,
            )
            session.add(row)
        else:
            _merge(row, ports, families)
            row.status = "allowed"
        row.decided_by = principal.email
        row.decided_at = now
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
            origin=origin,
            reason=reason[:500],
        )
        rules.append(row)
    return rules


def refusal_409(items: list[EgressVerdict], organisation: str) -> ApiError:
    hosts = ", ".join(sorted({f"{v.host}" for v in items}))
    body = EgressRequired(verdicts=items)
    return ApiError(
        409,
        "Network access required",
        f"{organisation} has not opened {hosts}: an administrator allows it (egress_required)",
        type_="https://choregos.dev/problems/egress-required",
        extra=body.model_dump(mode="json"),
    )


async def ensure_or_409(
    session: AsyncSession,
    principal: Principal,
    organisation: Organization,
    targets: list[Target],
    *,
    allow_egress: bool,
    origin: str,
    reason: str = "",
    settings: Settings | None = None,
) -> None:
    """Called by every governed "add" before its side effect. Every target already allowed (or the
    list not enforced): nothing happens. Otherwise 409 `egress_required` with the verdicts — or,
    with `allow_egress` and the right to grant, the missing hosts are opened IN THE CALLER'S
    transaction. `allow_egress` without the right is a 403 naming who can."""
    settings = settings or get_settings()
    if mode_of(settings) != "enforce" or not targets:
        return
    items = await verdicts(
        session, organisation, principal, targets, settings=settings, reason=reason, origin=origin
    )
    missing = [t for t, v in zip(targets, items, strict=True) if v.org != "allowed"]
    if not missing:
        return
    if not allow_egress:
        raise refusal_409([v for v in items if v.org != "allowed"], organisation.slug)
    if not principal.can(Permission.TOOLS_GRANT, organisation.slug):
        who = ", ".join(a.email for a in items[0].approvers) or "an organisation administrator"
        raise forbidden(
            f"opening {', '.join(sorted({t.host for t in missing}))} to {organisation.slug} is decided "
            f"by its administrators ({who}): ask them"
        )
    await allow_hosts(session, principal, organisation, missing, origin=origin, reason=reason)


async def count_open_requests(session: AsyncSession, org_id: str, email: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(OrgEgressRule)
                .where(
                    OrgEgressRule.org_id == org_id,
                    OrgEgressRule.status == "requested",
                    OrgEgressRule.requested_by == email,
                )
            )
        ).scalar_one()
    )


def rule_dto(row: OrgEgressRule) -> EgressRule:
    return EgressRule.model_validate(row)


def skill_import_targets(repo_url: str) -> list[Target]:
    """What a git skill import calls: GitHub's API and its tarball host, or GitLab."""
    from .skill_sources import SourceRefused, parse_repo_url

    try:
        source = parse_repo_url(repo_url)
    except SourceRefused as refusal:
        from ..errors import unprocessable

        raise unprocessable(str(refusal)) from refusal
    hosts = ["api.github.com", "codeload.github.com"] if source.host == "github.com" else [source.host]
    return targets_of_hosts([(h, 443) for h in hosts], "api")


def knowledge_source_targets(kind: str, config: dict[str, Any]) -> list[Target]:
    """A `web` source's host, a `git` source's repository host (plus GitHub's API for a GitHub App).
    A connected kind (`sharepoint`, `confluence`) gets its hosts from its connector, which is gated
    when the connector is declared."""
    if kind not in ("web", "git"):
        return []
    hosts = [hp for hp in (host_port_of(config.get("url")),) if hp]
    if kind == "git" and any(h == "github.com" for h, _ in hosts) and config.get("github_app"):
        hosts.append(("api.github.com", 443))
    return targets_of_hosts(hosts, "api") + targets_of_hosts(hosts, "workers")


def remote_agent_targets(card_url: str) -> list[Target]:
    """A remote agent's card host: read by the API now, called by the runner pods during a step. The
    endpoint and token hosts the card names are only known once it is read."""
    hp = host_port_of(card_url)
    if hp is None:
        return []
    return targets_of_hosts([hp], "api") + targets_of_hosts([hp], "runners")


async def egress_gate_for_remote_agent(
    session: AsyncSession,
    principal: Principal,
    organisation: Organization,
    spec: Any,
    allow_egress: bool,
) -> None:
    """Registering or publishing a remote (`a2a`) agent opens its card host first (S39-22)."""
    runtime = getattr(spec, "runtime", None)
    card_url = getattr(runtime, "card_url", None)
    if not card_url:
        return
    await ensure_or_409(
        session,
        principal,
        organisation,
        remote_agent_targets(card_url),
        allow_egress=allow_egress,
        origin=f"remote-agent:{card_url}",
        reason=f"the remote agent at {card_url}",
    )


#: The connector kinds whose hosts are gated today. The REST connectors (Jira, GitLab, Slack...)
#: join in phase 2, first in `warn`: their hosts are seeded by the migration, and a wrong seed would
#: break trackers.
GATED_CONNECTOR_KINDS = frozenset({"mcp"})


async def egress_gate_for_connector(  # noqa: PLR0913 - the connector's identity, then the gesture
    session: AsyncSession,
    principal: Principal,
    organisation: Organization,
    kind: str,
    type_name: str,
    config: dict[str, Any],
    name: str,
    allow_egress: bool,
    origin: str | None = None,
) -> None:
    if kind not in GATED_CONNECTOR_KINDS:
        return
    await ensure_or_409(
        session,
        principal,
        organisation,
        targets_of_connector(kind, type_name, config),
        allow_egress=allow_egress,
        origin=origin or f"org-connector:{name}",
        reason=f"the {type_name} connector `{name}`",
    )
