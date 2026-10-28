# SPDX-License-Identifier: Apache-2.0
"""Each organisation signs in with its own identity provider (ADR 0107).

The core knows one identity provider, the instance's (`CHOREGOS_OIDC_*`). A tenant that brings its
own (an OIDC issuer, a SAML IdP) needs three things the core cannot decide alone: where that
provider's settings are stored, which e-mail domains the tenant owns, and how a SAML assertion is
verified. An extension (ADR 0024: the enterprise edition) answers them by declaring ONE resolver:

    from choregos_api.tenant_sign_in import OidcProvider, TenantSignIn, declare_sign_in_resolver

    async def resolve(session, hint):
        # hint.org: the organisation named in the sign-in link (`/auth/login?org=acme`)
        # hint.email_domain: the domain of the address typed on the sign-in page
        row = await my_storage.find(session, org=hint.org, domain=hint.email_domain)
        if row is None:
            return None                      # the instance's identity provider
        return TenantSignIn(org=row.org, oidc=OidcProvider(issuer=row.issuer, ...))

    def plug():
        declare_sign_in_resolver(resolve)

What the core guarantees, whatever the resolver returns, and why each guarantee exists:

- **An address is signed in only by the provider of the tenant that owns its domain.** After a
  tenant's provider vouches for `bob@example.com`, the core asks the resolver which tenant owns
  `example.com` and refuses unless it is the same one. Without it, any tenant's provider could
  assert any address and sign in as a member of another tenant.
- **A tenant's provider grants roles in its tenant only.** A group naming another organisation
  (`choregos:<other>:org-admins`) is dropped; a bare group applies to the tenant.
- **A tenant's subject never collides with another provider's.** It is stored as
  `tenant:<org id>:<sub>`: two providers may well issue the same `sub`.

A resolver must only map a domain to a tenant that PROVED it owns it (a DNS record, for instance):
the core trusts that mapping, it has no other source for it.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

#: The resolver an extension declared, if any. A container rather than a module variable, as in
#: `edition.py`.
_RESOLVER: dict[str, Any] = {}


@dataclass(frozen=True)
class SignInHint:
    """What the sign-in request says about who is signing in. Either may be missing."""

    org: str | None = None
    email_domain: str | None = None


@dataclass(frozen=True)
class OidcProvider:
    """A tenant's OpenID Connect provider: every option the instance's `CHOREGOS_OIDC_*` has, and more.

    The three endpoints are for a provider that publishes no discovery document: set all three or
    none. `require_email_verified` refuses an address the provider marks `email_verified: false`
    (a provider that omits the claim, such as Entra ID, is accepted). `groups_claim` names the
    userinfo claim that carries the groups.
    """

    issuer: str
    client_id: str
    client_secret: str = ""
    scopes: str = "openid profile email groups"
    authorization_endpoint: str = ""
    token_endpoint: str = ""
    userinfo_endpoint: str = ""
    require_email_verified: bool = True
    groups_claim: str = "groups"

    def __post_init__(self) -> None:
        if not self.issuer.startswith(("https://", "http://")) or not self.client_id:
            raise ValueError("an OIDC provider needs an http(s) issuer and a client id")
        explicit = [self.authorization_endpoint, self.token_endpoint, self.userinfo_endpoint]
        if any(explicit) and not all(explicit):
            raise ValueError("set the authorization, token and userinfo endpoints together, or none")


@dataclass(frozen=True)
class TenantSignIn:
    """How the members of organisation `org` sign in: an OIDC provider the core drives, or the
    path of an extension's route (`start_path`) that starts another protocol, SAML for instance.

    That route verifies the identity itself, then calls `routers.auth.terminer_la_connexion` with
    `org=` set to this tenant, so the confinement above applies to it too.
    """

    org: str
    oidc: OidcProvider | None = None
    start_path: str | None = None

    def __post_init__(self) -> None:
        if not self.org:
            raise ValueError("a tenant sign-in names its organisation")
        if (self.oidc is None) == (self.start_path is None):
            raise ValueError("a tenant sign-in has either an OIDC provider or a start path, not both")
        if self.start_path is not None and (
            not self.start_path.startswith("/") or self.start_path.startswith("//")
        ):
            raise ValueError("the start path is a path on this API, such as /api/v1/saml/acme/login")


def declare_sign_in_resolver(resolver: Any) -> None:
    """Declares `resolver(session, hint: SignInHint) -> TenantSignIn | None`, sync or async.

    `None` means the instance's identity provider. One resolver per deployment: a second
    declaration replaces the first. An exception is not a `None`: it fails the sign-in.
    """
    _RESOLVER["resolver"] = resolver


def has_resolver() -> bool:
    return "resolver" in _RESOLVER


def reset() -> None:
    """For tests: back to the instance's identity provider only."""
    _RESOLVER.clear()


async def resolve(session: Any, hint: SignInHint) -> TenantSignIn | None:
    resolver = _RESOLVER.get("resolver")
    if resolver is None or not (hint.org or hint.email_domain):
        return None
    found = resolver(session, hint)
    if inspect.isawaitable(found):
        found = await found
    if found is not None and not isinstance(found, TenantSignIn):
        raise TypeError(f"the sign-in resolver returned {type(found).__name__}, not a TenantSignIn")
    return found


def email_domain(address: str | None) -> str | None:
    """The domain of an e-mail address, lowercased; `None` when there is no `@` or nothing after it."""
    if not address or "@" not in address:
        return None
    domain = address.rpartition("@")[2].strip().lower()
    return domain or None
