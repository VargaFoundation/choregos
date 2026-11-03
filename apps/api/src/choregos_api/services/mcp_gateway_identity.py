# SPDX-License-Identifier: Apache-2.0
"""The identity assertion Choregos signs for an organisation's MCP gateway (S46-07, ADR 0105).

Each organisation has its own ES256 key, never the deployment-wide run-token key (ADR 0077). The
private key is sealed in the organisation's secret store (ADR 0058); the public keys live in
`Organization.settings` so that the public JWKS route never reads the store. A lost key fails
closed with `AssertionKeyMissing`, never a call without an assertion.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

import jwt
from choregos_adapters.errors import ConfigurationError
from choregos_core import utcnow
from choregos_core.redaction import remember_secret
from choregos_core.secrets import SecretIntrouvable
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..config import Settings, get_settings
from ..db.models import Action, Organization, OrgConnector, Project, Run, WorkItem
from ..schemas.mcp_gateway import McpGatewayAssertion, McpGatewayAssertionKey
from ..secret_store import SecretStore, SecretStoreUnavailable, get_secret_store

SETTINGS_KEY = "mcp_gateway_assertion"
#: One sealed PEM per kid, at `{SECRET_PREFIX}/{kid}` (ADR 0058).
SECRET_PREFIX = "org/mcp-gateway/assertion-key"  # noqa: S105 - a name in the secret store, not a secret
ASSERTION_TTL_S = 60
MAX_TTL_S = 120


class GatewayRefusal(ConfigurationError):  # noqa: N818 - a typed refusal, named by its code
    """A refusal raised before anything leaves for the MCP gateway (ADR 0105)."""

    code: ClassVar[str] = "gateway_refused"


class AssertionKeyMissing(GatewayRefusal):
    code: ClassVar[str] = "gateway_assertion_key_missing"


@dataclass(frozen=True)
class CallIdentity:
    """Who a call is made for. `org_id` is the identity that survives a rename (ADR 0090); `org` is
    the slug, for display."""

    org: str
    org_id: str
    subject: str
    purpose: str
    project: str | None = None
    run: str | None = None
    work_item: str | None = None
    agent: str | None = None
    initiator: str | None = None
    action: str | None = None
    connector: str | None = None
    server: str | None = None
    tool: str | None = None
    approved_by: tuple[str, ...] = ()

    def claims(self) -> dict[str, Any]:
        out: dict[str, Any] = {"sub": self.subject}
        for name in (
            "org",
            "org_id",
            "purpose",
            "project",
            "run",
            "work_item",
            "agent",
            "initiator",
            "action",
            "connector",
            "server",
            "tool",
        ):
            value = getattr(self, name)
            if value:
                out[name] = value
        if self.approved_by:
            out["approved_by"] = list(self.approved_by)
        return out


def _thumbprint_kid(jwk: dict[str, Any]) -> str:
    """The RFC 7638 thumbprint members, hashed: the same key always has the same kid."""
    members = {k: jwk[k] for k in ("crv", "kty", "x", "y")}
    digest = hashlib.sha256(json.dumps(members, separators=(",", ":"), sort_keys=True).encode())
    return digest.hexdigest()[:32]


def _stored(organisation: Organization) -> dict[str, Any]:
    found = (organisation.settings or {}).get(SETTINGS_KEY)
    return found if isinstance(found, dict) else {}


def issuer_of(organisation_or_slug: Organization | str, settings: Settings | None = None) -> str:
    """The `iss` of the assertions. Stored on the first key: an organisation's slug is an alias (ADR
    0090) and the gateway compares `iss` verbatim, so a rename must not change it. The old JWKS URL
    keeps resolving through the alias middleware until the alias is released. Before any key exists,
    the current slug's URL."""
    if isinstance(organisation_or_slug, Organization):
        stored = _stored(organisation_or_slug).get("issuer")
        if isinstance(stored, str) and stored:
            return stored
        slug = organisation_or_slug.slug
    else:
        slug = organisation_or_slug
    base = (settings or get_settings()).public_url.rstrip("/")
    return f"{base}/api/v1/orgs/{slug}/mcp-gateway"


def jwks_url_of(organisation_or_slug: Organization | str, settings: Settings | None = None) -> str:
    """The JWKS URL: the stored one once a key exists (see `issuer_of`)."""
    if isinstance(organisation_or_slug, Organization):
        stored = _stored(organisation_or_slug).get("jwks_url")
        if isinstance(stored, str) and stored:
            return stored
    return issuer_of(organisation_or_slug, settings) + "/jwks.json"


def _secret_name(kid: str) -> str:
    return f"{SECRET_PREFIX}/{kid}"


async def ensure_assertion_key(
    session: AsyncSession, organisation: Organization, actor: str | None, store: SecretStore | None = None
) -> str:
    """The current kid, creating the first key when there is none."""
    current = _stored(organisation).get("current")
    if isinstance(current, str) and current:
        return current
    return (await rotate_assertion_key(session, organisation, actor, store))[0]


async def rotate_assertion_key(
    session: AsyncSession, organisation: Organization, actor: str | None, store: SecretStore | None = None
) -> tuple[str, str | None]:
    """A new key signs; the previous one stays published; older ones are dropped, PEM included.
    Returns `(new, previous)`."""
    store = store or get_secret_store()
    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    remember_secret(pem)
    jwk = dict(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key(), as_dict=True))
    kid = _thumbprint_kid(jwk)
    jwk.update({"use": "sig", "alg": "ES256", "kid": kid})
    await store.put(session, organisation.id, _secret_name(kid), pem, actor)

    state = _stored(organisation)
    raw_previous = state.get("current")
    previous = raw_previous if isinstance(raw_previous, str) else None
    old = [k for k in state.get("keys", []) if isinstance(k, dict)]
    kept = [k for k in old if k.get("kid") == previous and previous != kid]
    dropped = [k["kid"] for k in old if k not in kept and k.get("kid") != kid]
    entry = {"kid": kid, "jwk": jwk, "created_at": utcnow().isoformat(), "created_by": actor}
    settings = dict(organisation.settings or {})
    settings[SETTINGS_KEY] = {
        "issuer": issuer_of(organisation),
        "jwks_url": jwks_url_of(organisation),
        "current": kid,
        "keys": [entry, *kept],
    }
    organisation.settings = settings
    flag_modified(organisation, "settings")
    await session.flush()
    for gone in dropped:
        await store.delete(session, organisation.id, _secret_name(gone))
    return kid, (previous if previous != kid else None)


def jwks_of(organisation: Organization) -> dict[str, Any]:
    """The public keys of the organisation, never a private member."""
    keys: list[dict[str, Any]] = []
    for key in _stored(organisation).get("keys", []):
        jwk = key.get("jwk") if isinstance(key, dict) else None
        if isinstance(jwk, dict):
            keys.append({k: v for k, v in jwk.items() if k != "d"})
    return {"keys": keys}


def assertion_state(organisation: Organization, settings: Settings | None = None) -> McpGatewayAssertion:
    state = _stored(organisation)
    current = state.get("current")
    keys = [
        McpGatewayAssertionKey(kid=k["kid"], created_at=str(k["created_at"]), current=k["kid"] == current)
        for k in state.get("keys", [])
        if isinstance(k, dict)
    ]
    return McpGatewayAssertion(
        issuer=issuer_of(organisation, settings), jwks_url=jwks_url_of(organisation, settings), keys=keys
    )


async def signing_key(
    session: AsyncSession, organisation: Organization, store: SecretStore | None = None
) -> tuple[str, str]:
    """`(kid, pem)` of the current key. Fails closed when there is none or its PEM is lost."""
    missing = AssertionKeyMissing(
        f"the MCP gateway assertion key of {organisation.slug} is missing: "
        "an administrator rotates it on the Administration page"
    )
    current = _stored(organisation).get("current")
    if not isinstance(current, str) or not current:
        raise missing
    name = _secret_name(current)
    try:
        pem = (await (store or get_secret_store()).get_many(session, organisation.id, [name]))[name]
    except (SecretIntrouvable, SecretStoreUnavailable) as lost:
        raise missing from lost
    remember_secret(pem)
    return current, pem


def mint_assertion(
    kid: str,
    pem: str,
    *,
    issuer: str,
    audience: str,
    identity: CallIdentity,
    now: datetime | None = None,
) -> str:
    """The short-lived signed assertion of a call identity."""
    issued = int((now or utcnow()).timestamp())
    payload = {
        "iss": issuer,
        "aud": audience,
        "iat": issued,
        "exp": issued + ASSERTION_TTL_S,
        "jti": uuid.uuid4().hex,
        **identity.claims(),
    }
    return jwt.encode(payload, pem, algorithm="ES256", headers={"kid": kid, "typ": "JWT"})


async def _org_and_work_item(
    session: AsyncSession, org_id: str, work_item_id: str | None
) -> tuple[Organization, WorkItem | None]:
    organisation = await session.get(Organization, org_id)
    assert organisation is not None
    item = await session.get(WorkItem, work_item_id) if work_item_id else None
    return organisation, item


async def identity_of_run(
    session: AsyncSession, run: Run | None, project: Project | None, connector: OrgConnector, tool: str
) -> CallIdentity:
    """Who a tool call made by a run (or, with no run, by an agent session) is made for (ADR 0105)."""
    organisation, item = await _org_and_work_item(
        session, connector.org_id, run.work_item_id if run else None
    )
    common: dict[str, Any] = {
        "org": organisation.slug,
        "org_id": organisation.id,
        "project": project.slug if project is not None else None,
        "connector": connector.name,
        "server": (connector.config or {}).get("server"),
        "tool": tool,
        "purpose": "tool_call",
    }
    if run is None:
        subject = f"project:{project.slug}" if project is not None else f"org:{organisation.slug}"
        return CallIdentity(subject=subject, **common)
    return CallIdentity(
        subject=f"run:{run.id}",
        run=run.id,
        work_item=item.tracker_key if item is not None else None,
        agent=f"{run.agent_slug}@{run.agent_version}" if run.agent_slug else None,
        initiator=run.initiated_by,
        **common,
    )


async def identity_of_action(
    session: AsyncSession, action: Action, project: Project, connector: OrgConnector, tool: str
) -> CallIdentity:
    """Who an approved governed write is made for: the action, and the people who approved it."""
    organisation, item = await _org_and_work_item(session, connector.org_id, action.work_item_id)
    run = await session.get(Run, action.run_id) if action.run_id else None
    return CallIdentity(
        org=organisation.slug,
        org_id=organisation.id,
        subject=f"action:{action.id}",
        purpose="action",
        project=project.slug,
        run=action.run_id,
        work_item=item.tracker_key if item is not None else None,
        agent=f"{run.agent_slug}@{run.agent_version}" if run is not None and run.agent_slug else None,
        initiator=(item.initiated_by if item is not None else None) or (run.initiated_by if run else None),
        action=action.id,
        connector=connector.name,
        server=(connector.config or {}).get("server"),
        tool=tool,
        approved_by=tuple(
            str(d["by"]) for d in action.decisions or [] if d.get("decision") == "approve" and d.get("by")
        ),
    )


def identity_of_person(
    organisation: Organization, email: str, connector: OrgConnector, purpose: str
) -> CallIdentity:
    """Who a call made by a person (a discovery, a test) is made for."""
    return CallIdentity(
        org=organisation.slug,
        org_id=organisation.id,
        subject=f"person:{email}",
        purpose=purpose,
        initiator=f"person:{email}",
        connector=connector.name,
        server=(connector.config or {}).get("server"),
    )
