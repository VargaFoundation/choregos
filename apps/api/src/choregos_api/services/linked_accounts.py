# SPDX-License-Identifier: Apache-2.0
"""A member's accounts on code forges (ADR 0161): linked by the member and proven, or declared.

An organisation administrator configures a forge's OAuth application as an organisation connector of kind
`forge_account` (`github-oauth`, `gitlab-oauth`, `forgejo-oauth`): its `base_url`, its `client_id`, its
`client_secret` sealed like any connector secret (ADR 0058), its hosts in the egress allowlist.

A member links their OWN account: `begin` signs a state bound to them and to their browser session and sets
a handshake cookie holding the PKCE verifier; the forge sends the browser back to `complete`, which checks
both, exchanges the code under the organisation's egress allowlist, reads the forge's user and records it
`verified`. The access token is used for that one read and never stored.

An administrator may still declare a link by numeric id and login (ADR 0102): it is stored NOT verified.
Whichever way it was written, only `external_id` is ever matched.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Final

import httpx
from choregos_adapters.errors import AdapterError
from choregos_adapters.forge_accounts import (
    DEFAULT_BASE_URL,
    GITHUB_COM,
    KIND,
    ForgeAccountError,
    ForgeAccounts,
    forge_accounts,
    normalise_base_url,
    provider_of_type,
)
from choregos_core import utcnow
from choregos_core.secrets import SecretNotFound
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..config import Settings, get_settings
from ..db.models import LinkedAccount, Membership, Organization, OrgConnector
from ..errors import conflict, not_found, unprocessable
from ..rbac import Principal
from ..secret_store import SecretStoreUnavailable, resolved_configuration
from ..security import read_session, sign_session
from .egress import egress_scope

HANDSHAKE_COOKIE: Final = "choregos_forge_link"
HANDSHAKE_S: Final = 600
CALLBACK_PATH: Final = "/api/v1/auth/linked-accounts/callback"
#: Where the handshake cookie is sent back: the callback only.
COOKIE_PATH: Final = "/api/v1/auth/linked-accounts"
_STATE_PURPOSE: Final = "forge-link-state"
_COOKIE_PURPOSE: Final = "forge-link-handshake"

_TRANSPORT: list[httpx.AsyncBaseTransport] = []


def set_forge_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """For tests: the transport the calls to the forges go through."""
    _TRANSPORT.clear()
    if transport is not None:
        _TRANSPORT.append(transport)


def _transport(settings: Settings) -> httpx.AsyncBaseTransport | None:
    if _TRANSPORT:
        return _TRANSPORT[0]
    if settings.fakes:
        from choregos_adapters.fakes.forge_accounts import current_fake_forge

        return current_fake_forge().transport()
    return None


class LinkRefused(Exception):  # noqa: N818 - a named refusal, rendered as the console's `link_error`
    """The callback did not link the account. `code` is what the console says; no token is in it."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True, slots=True)
class Instance:
    """A forge instance an organisation configured: the connector that holds its OAuth application."""

    org: Organization
    connector: OrgConnector
    provider: str
    base_url: str


@dataclass(frozen=True, slots=True)
class Declared:
    """What an administrator declares of a member's account: never proven."""

    provider: str
    base_url: str
    external_id: str
    login: str


def redirect_uri(settings: Settings | None = None) -> str:
    return (settings or get_settings()).api_url.rstrip("/") + CALLBACK_PATH


def base_url_for(provider: str, given: str | None) -> str:
    """The instance a declaration names: the provider's default when it has one, normalised."""
    raw = given or DEFAULT_BASE_URL.get(provider)
    if not raw:
        raise unprocessable(f"a `{provider}` account needs the instance's `base_url`")
    try:
        normalised = normalise_base_url(raw)
    except AdapterError as bad:
        raise unprocessable(str(bad)) from bad
    if not normalised.startswith("https://"):
        raise unprocessable("`base_url` must use https")
    return normalised


def _instance_of(org: Organization, connector: OrgConnector) -> Instance | None:
    provider = provider_of_type(connector.type) if connector.kind == KIND else None
    if provider is None:
        return None
    try:
        base = normalise_base_url(
            str((connector.config or {}).get("base_url") or DEFAULT_BASE_URL.get(provider, ""))
        )
    except AdapterError:
        return None
    return Instance(org=org, connector=connector, provider=provider, base_url=base)


async def instances_for(session: AsyncSession, principal: Principal) -> list[Instance]:
    """The forge instances configured in the caller's organisations."""
    if not principal.org_roles:
        return []
    rows = (
        await session.execute(
            select(OrgConnector, Organization)
            .join(Organization, Organization.id == OrgConnector.org_id)
            .where(OrgConnector.kind == KIND, Organization.slug.in_(list(principal.org_roles)))
            .order_by(Organization.slug, OrgConnector.name)
        )
    ).all()
    found = [_instance_of(org, connector) for connector, org in rows]
    return [i for i in found if i is not None]


async def _instance(session: AsyncSession, org: Organization, name: str, provider: str) -> Instance:
    connector = (
        await session.execute(
            select(OrgConnector).where(
                OrgConnector.org_id == org.id, OrgConnector.name == name, OrgConnector.kind == KIND
            )
        )
    ).scalar_one_or_none()
    instance = _instance_of(org, connector) if connector is not None else None
    if instance is None or instance.provider != provider:
        raise not_found("Forge instance", name)
    return instance


async def _adapter(session: AsyncSession, instance: Instance) -> ForgeAccounts:
    connector = instance.connector
    try:
        resolved = await resolved_configuration(
            session, instance.org.id, connector.kind, connector.type, connector.config, connector.secret_refs
        )
        return forge_accounts(instance.provider, resolved, transport=_transport(get_settings()))
    except (SecretNotFound, SecretStoreUnavailable, AdapterError) as failure:
        raise LinkRefused("instance_unusable", f"`{connector.name}` cannot be used: {failure}") from failure


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


async def is_member(session: AsyncSession, org_id: str, user_id: str) -> bool:
    """A member of the organisation itself (a project role is not one)."""
    found = (
        await session.execute(
            select(Membership.id).where(
                Membership.org_id == org_id, Membership.user_id == user_id, Membership.project_id.is_(None)
            )
        )
    ).first()
    return found is not None


async def begin(
    session: AsyncSession, principal: Principal, org: str, provider: str, instance_name: str
) -> tuple[str, str]:
    """`(authorization URL, handshake cookie)` for the member to sign in to the forge."""
    from .organisations import organisation_by_slug

    if org not in principal.org_roles:
        raise not_found("Organisation", org)
    organisation = await organisation_by_slug(session, org)
    if not await is_member(session, organisation.id, principal.user_id):
        raise not_found("Organisation", org)
    instance = await _instance(session, organisation, instance_name, provider)
    try:
        adapter = await _adapter(session, instance)
    except LinkRefused as refused:
        raise unprocessable(str(refused)) from refused
    settings = get_settings()
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce()
    expiry = int(time.time()) + HANDSHAKE_S
    state = sign_session(
        {
            "u": principal.user_id,
            "s": principal.session_id or "",
            "o": organisation.id,
            "c": instance_name,
            "n": nonce,
            "exp": expiry,
        },
        settings,
        purpose=_STATE_PURPOSE,
    )
    cookie = sign_session({"n": nonce, "v": verifier, "exp": expiry}, settings, purpose=_COOKIE_PURPOSE)
    return adapter.authorize_url(
        redirect_uri=redirect_uri(settings), state=state, code_challenge=challenge
    ), cookie


async def complete(
    session: AsyncSession, principal: Principal, *, code: str, state: str, cookie: str
) -> LinkedAccount:
    """The callback: checks the state and the handshake, exchanges the code, records the verified link."""
    settings = get_settings()
    claims = read_session(state, settings, purpose=_STATE_PURPOSE)
    handshake = read_session(cookie, settings, purpose=_COOKIE_PURPOSE) if cookie else None
    if claims is None or handshake is None or claims.get("n") != handshake.get("n"):
        raise LinkRefused("handshake", "the link handshake is invalid or expired: start again")
    if claims.get("u") != principal.user_id or str(claims.get("s") or "") != (principal.session_id or ""):
        raise LinkRefused("handshake", "this link was started by another person or another session")
    organisation = await session.get(Organization, str(claims["o"]))
    if organisation is None or organisation.slug not in principal.org_roles:
        raise LinkRefused("handshake", "the organisation of this link is not yours")
    if not await is_member(session, organisation.id, principal.user_id):
        raise LinkRefused("handshake", "the organisation of this link is not yours")
    connector = (
        await session.execute(
            select(OrgConnector).where(
                OrgConnector.org_id == organisation.id,
                OrgConnector.name == str(claims["c"]),
                OrgConnector.kind == KIND,
            )
        )
    ).scalar_one_or_none()
    instance = _instance_of(organisation, connector) if connector is not None else None
    if instance is None:
        raise LinkRefused("instance_gone", "the forge instance of this link is no longer configured")
    adapter = await _adapter(session, instance)
    try:
        # Under the organisation's egress allowlist, like every call to a source.
        async with egress_scope(session, organisation, "api", f"forge-link:{instance.connector.name}"):
            token = await adapter.exchange_code(
                code=code, redirect_uri=redirect_uri(settings), code_verifier=str(handshake["v"])
            )
            user = await adapter.read_user(token)
    except ForgeAccountError as refused:
        raise LinkRefused("forge_refused", f"the forge refused ({refused.code})") from refused
    except httpx.HTTPError as blocked:
        raise LinkRefused("forge_unreachable", "the forge could not be reached") from blocked
    holder = await _holder(session, organisation.id, instance.provider, instance.base_url, user.external_id)
    if holder is not None and holder.user_id != principal.user_id:
        raise LinkRefused("taken", "this forge account is already linked to another member")
    row = holder or await _of_member(
        session, organisation.id, principal.user_id, instance.provider, instance.base_url
    )
    if row is None:
        row = LinkedAccount(
            org_id=organisation.id,
            user_id=principal.user_id,
            provider=instance.provider,
            base_url=instance.base_url,
        )
        session.add(row)
    previous = row.external_id if row.external_id != user.external_id else None
    row.external_id = user.external_id
    row.login = user.login
    row.avatar_url = user.avatar_url
    row.profile_url = user.profile_url
    row.verified = True
    row.verified_at = utcnow()
    row.connector = instance.connector.name
    row.created_by = principal.email
    await session.flush()
    await session.refresh(row)
    await record(
        session,
        principal,
        "linked_account.verify",
        org_id=organisation.id,
        target_type="user",
        target_id=principal.user_id,
        provider=instance.provider,
        base_url=instance.base_url,
        external_id=user.external_id,
        login=user.login,
        connector=instance.connector.name,
        replaced=previous,
    )
    return row


async def _holder(
    session: AsyncSession, org_id: str, provider: str, base_url: str, external_id: str
) -> LinkedAccount | None:
    return (
        await session.execute(
            select(LinkedAccount).where(
                LinkedAccount.org_id == org_id,
                LinkedAccount.provider == provider,
                LinkedAccount.base_url == base_url,
                LinkedAccount.external_id == external_id,
            )
        )
    ).scalar_one_or_none()


async def _of_member(
    session: AsyncSession, org_id: str, user_id: str, provider: str, base_url: str
) -> LinkedAccount | None:
    return (
        await session.execute(
            select(LinkedAccount).where(
                LinkedAccount.org_id == org_id,
                LinkedAccount.user_id == user_id,
                LinkedAccount.provider == provider,
                LinkedAccount.base_url == base_url,
            )
        )
    ).scalar_one_or_none()


async def declare(
    session: AsyncSession,
    principal: Principal,
    org_id: str,
    user_id: str,
    account: Declared,
    action: str = "linked_account.declare",
) -> LinkedAccount:
    """An administrator declares a member's account on an instance: NOT verified, replacing the member's
    link there. A link the member proved for the same account stays verified: a declaration never
    downgrades a proof, and never takes an account another member holds."""
    provider, base_url, external_id, login = (
        account.provider,
        account.base_url,
        account.external_id,
        account.login,
    )
    holder = await _holder(session, org_id, provider, base_url, external_id)
    if holder is not None and holder.user_id != user_id:
        raise conflict(f"this {provider} user id is already linked to another member")
    row = await _of_member(session, org_id, user_id, provider, base_url)
    if row is None:
        row = LinkedAccount(org_id=org_id, user_id=user_id, provider=provider, base_url=base_url)
        session.add(row)
    if row.external_id != external_id or not row.verified:
        row.verified = False
        row.verified_at = None
        row.connector = None
        row.avatar_url = None
        row.profile_url = None
        row.created_by = principal.email
    row.external_id = external_id
    row.login = login
    await session.flush()
    await session.refresh(row)
    audit = {"tracker": provider} if action.startswith("tracker_identity.") else {"provider": provider}
    await record(
        session,
        principal,
        action,
        org_id=org_id,
        target_type="user",
        target_id=user_id,
        base_url=base_url,
        external_id=external_id,
        login=login,
        **audit,
    )
    return row


async def remove(
    session: AsyncSession, principal: Principal, row: LinkedAccount, action: str = "linked_account.delete"
) -> None:
    payload = (
        {"tracker": row.provider} if action.startswith("tracker_identity.") else {"provider": row.provider}
    )
    await record(
        session,
        principal,
        action,
        org_id=row.org_id,
        target_type="user",
        target_id=row.user_id,
        base_url=row.base_url,
        external_id=row.external_id,
        verified=row.verified,
        **payload,
    )
    await session.delete(row)
    await session.flush()


async def github_member(session: AsyncSession, org_id: str, external_id: str) -> LinkedAccount | None:
    """The link a signed github.com comment's numeric author id names in the organisation, if any.

    Declared and verified links both answer, as before ADR 0161: requiring `verified` is a later switch."""
    return await _holder(session, org_id, "github", GITHUB_COM, external_id)
