# SPDX-License-Identifier: Apache-2.0
"""Who knocks at the door: a token with an MCP scope, the person who used it, and their project, if any."""

from __future__ import annotations

from dataclasses import dataclass

from choregos_core import utcnow
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.models import ApiToken, MachineAccount, Organization, Project, User
from ..db.session import ALL_ORGS, limit_to_organisations
from ..deps import (
    OUTPOST_REFUSAL,
    _aware,
    _principal_from_user,
    presents_outpost_signature,
    record_usage,
)
from ..rbac import Principal
from ..schemas.agents import grant_name
from ..security import hash_api_token
from .oauth import TokenRefused, issuer_of, validate

MCP_SCOPES = frozenset({"mcp:read", "mcp:write"})
#: The A2A door's scope (ADR 0078): one scope, since a task is opened or read, never decided.
SCOPES_A2A = frozenset({"a2a"})


class Refusal(Exception):  # noqa: N818 - a decision of the door, not a failure
    """The door refuses before any MCP exchange: rendered as HTTP, with `WWW-Authenticate`."""

    def __init__(
        self,
        status: int,
        message: str,
        *,
        error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error = error


@dataclass(slots=True, init=False)
class Caller:
    principal: Principal
    #: The `chg_` token, or `None` for an OAuth token from the identity provider.
    token: ApiToken | None
    scopes: frozenset[str]
    #: What identifies the client for the rate and the audit: `jeton:<id>` or `oauth:<azp>:<sub>`.
    key: str
    bound_project: Project | None
    bound_project_org: str | None
    #: The external agent this client embodies (ADR 0033), if it is attached to one.
    agent: str | None
    #: Its organisation: a call that targets no project is recorded under it in the audit log.
    agent_org_id: str | None
    #: The door's tools the agent's version allows (patterns); `None`: those of the person.
    reasons: tuple[str, ...] | None
    #: A machine account's own daily budget (ADR 0131); `None`: a person's.
    writes_per_day: int | None

    def __init__(  # noqa: PLR0913 - one parameter per field
        self,
        principal: Principal,
        token: ApiToken | None,
        scopes: frozenset[str],
        key: str = "",
        bound_project: Project | None = None,
        bound_project_org: str | None = None,
        agent: str | None = None,
        agent_org_id: str | None = None,
        reasons: tuple[str, ...] | None = None,
        writes_per_day: int | None = None,
    ) -> None:
        self.principal = principal
        self.token = token
        self.scopes = scopes
        self.key = key
        self.bound_project = bound_project
        self.bound_project_org = bound_project_org
        self.agent = agent
        self.agent_org_id = agent_org_id
        self.reasons = reasons
        self.writes_per_day = writes_per_day

    @property
    def writes(self) -> bool:
        return "mcp:write" in self.scopes


async def identify(
    session: AsyncSession,
    request: Request,
    settings: Settings,
    *,
    scopes: frozenset[str] = MCP_SCOPES,
    door: str = "MCP",
) -> Caller:
    """The bearer token, read at platform scope; the principal then bounds the RLS.

    `scopes` are those of the door asking: the MCP door's, or the A2A door's (ADR 0078), which
    shares everything else — OAuth, agents, revocation — and refuses `*` the same way."""
    if presents_outpost_signature(request):
        # An outpost takes its assignments at its own door and calls no tool or task of the platform
        # (ADR 0147 §3, ADR 0148 §1): refused here whatever token comes with the signature.
        raise Refusal(403, OUTPOST_REFUSAL)
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        wanted = " or ".join(sorted(scopes))
        raise Refusal(401, f"missing token: Authorization: Bearer chg_… (scope {wanted})")
    raw = header.split(" ", 1)[1].strip()
    await limit_to_organisations(session, ALL_ORGS)
    if not raw.startswith("chg_") and settings.mcp_oauth_enabled:
        return await _by_oauth(session, raw, settings, scopes=scopes)
    token = (
        await session.execute(select(ApiToken).where(ApiToken.hash == hash_api_token(raw)))
    ).scalar_one_or_none()
    if token is None:
        raise Refusal(401, "unknown or revoked token", error="invalid_token")
    caller = await caller_of_token(session, token, scopes=scopes, door=door)
    record_usage(token, request)
    return caller


async def _machine_budget(session: AsyncSession, user: User) -> int:
    """The daily budget of a machine account; an account that has ended opens nothing."""
    account = (
        await session.execute(select(MachineAccount).where(MachineAccount.user_id == user.id))
    ).scalar_one_or_none()
    if account is None or account.removed_at is not None:
        raise Refusal(401, "this machine account has ended", error="invalid_token")
    return account.writes_per_day


async def caller_of_token(
    session: AsyncSession, token: ApiToken, *, scopes: frozenset[str] = MCP_SCOPES, door: str = "MCP"
) -> Caller:
    """A `chg_` token read again without its request: expiry, scope, user, agent and project.

    The A2A door replays it before each push notification (ADR 0078 §5): a token revoked, expired,
    or whose agent was suspended since the registration stops the deliveries."""
    if token.expires_at is not None and _aware(token.expires_at) <= utcnow():
        raise Refusal(401, "expired token", error="invalid_token")
    token_scopes = frozenset(token.scopes or ["*"])
    if not token_scopes & scopes:
        # A `*` token opens the REST API: it does not belong in a client's configuration.
        raise Refusal(
            403,
            f"the {door} door refuses a token without its scope (`*` included): "
            f"create a {' or '.join(sorted(scopes))} token for this client",
            error="insufficient_scope",
        )
    user = await session.get(User, token.user_id)
    if user is None:
        raise Refusal(401, "orphaned token", error="invalid_token")
    principal = await _principal_from_user(session, user)
    caller = Caller(principal=principal, token=token, scopes=token_scopes, key=f"jeton:{token.id}")
    if user.is_service:
        caller.writes_per_day = await _machine_budget(session, user)
    await act_as_agent(session, caller, token_id=token.id)
    if token.project_id:
        project = await session.get(Project, token.project_id)
        if project is None:
            raise Refusal(401, "this token's project no longer exists", error="invalid_token")
        org = await session.get(Organization, project.org_id)
        caller.bound_project, caller.bound_project_org = project, org.slug if org else ""
    return caller


async def _by_oauth(
    session: AsyncSession, raw: str, settings: Settings, *, scopes: frozenset[str] = MCP_SCOPES
) -> Caller:
    """A token issued by the identity provider for the door (RFC 9728): the person is found by their
    `sub`, then by their verified e-mail. They must have signed in to the console once: the door
    creates nobody.

    A `sub` only means something at its issuer: it designates a console user only if the door and
    the console share the same identity provider. Otherwise only the verified e-mail links the two."""
    try:
        claims = await validate(raw, settings)
    except TokenRefused as refusal:
        raise Refusal(401, str(refusal), error="invalid_token") from refusal
    sub = str(claims["sub"])
    user = None
    if issuer_of(settings) == settings.oidc_issuer.rstrip("/"):
        user = (await session.execute(select(User).where(User.oidc_sub == sub))).scalar_one_or_none()
    if user is None and claims.get("email") and claims.get("email_verified"):
        user = (
            await session.execute(select(User).where(User.email == str(claims["email"])))
        ).scalar_one_or_none()
    if user is None:
        raise Refusal(
            403,
            "user unknown to Choregos: sign in to the console once",
            error="invalid_token",
        )
    if user.is_service:
        raise Refusal(403, "a machine account signs in with its own token", error="invalid_token")
    requested = set(str(claims.get("scope") or "").split()) & scopes
    if not requested and scopes != MCP_SCOPES:
        # Only the MCP door reads by default: opening an A2A task is asked for, never implied.
        raise Refusal(
            403,
            f"the identity provider did not grant the {' or '.join(sorted(scopes))} scope",
            error="insufficient_scope",
        )
    principal = await _principal_from_user(session, user)
    client = str(claims.get("azp") or claims.get("client_id") or "?")
    caller = Caller(
        principal=principal,
        token=None,
        # Without a requested MCP scope, reading: writing is asked for explicitly.
        scopes=frozenset(requested or {"mcp:read"}),
        key=f"oauth:{client}:{sub}",
    )
    await act_as_agent(session, caller, client_id=client)
    # The validated bearer may be exchanged for a source's audience in this request (ADR 0135).
    from ..services.identity_exchange import door_bearer

    door_bearer(user.id, raw)
    return caller


async def act_as_agent(
    session: AsyncSession, caller: Caller, *, token_id: str | None = None, client_id: str | None = None
) -> None:
    """A client attached to an external agent (ADR 0033) carries its identity and its limits.

    The revocation is read again HERE, on every call: a revoked, suspended or expired agent gets a
    401 on the next one. Its rights are the person's — the principal does not change — intersected
    with the door's tools that its version names (`mcp_servers` of a `choregos` connector).
    """
    from ..db.models import Agent, AgentCredential

    condition = (
        AgentCredential.api_token_id == token_id
        if token_id is not None
        else (AgentCredential.kind == "oauth_client") & (AgentCredential.client_id == client_id)
    )
    attachment = (
        await session.execute(select(AgentCredential).where(condition, AgentCredential.revoked_at.is_(None)))
    ).scalar_one_or_none()
    if attachment is None:
        return
    agent = await session.get(Agent, attachment.agent_id)
    if agent is None:
        return
    retired = agent.lifecycle == "retired"
    if (
        retired
        or agent.status != "active"
        or (agent.expires_at is not None and _aware(agent.expires_at) <= utcnow())
    ):
        state = "retired" if retired else agent.status if agent.status != "active" else "expired"
        raise Refusal(
            401,
            f"agent `{agent.slug}` is {state}: this client is no longer allowed in",
            error="invalid_token",
        )
    # The tool patterns of the revision in production (ADR 0071), not of the latest saved one.
    from ..services.agent_releases import production_spec

    spec = await production_spec(session, agent) or {}
    patterns = [
        grant_name(tool)
        for server in spec.get("mcp_servers", [])
        if server.get("connector") == "choregos"
        for tool in server.get("tools", [])
    ]
    caller.agent = agent.slug
    caller.agent_org_id = agent.org_id
    caller.reasons = (
        tuple(patterns)
        if any(s.get("connector") == "choregos" for s in spec.get("mcp_servers", []))
        else None
    )
