# 0108 — Each organisation signs in with its own identity provider

- **Status**: accepted, 2026-10-28 (product owner: "the solution must be multi-tenant: a customer must be able
  to come with its own SSO, so every SSO option must be configurable")
- **Concerns**: `choregos_api.tenant_sign_in` (`declare_sign_in_resolver`, `SignInHint`, `TenantSignIn`,
  `OidcProvider`), `GET /auth/login` (`org`, `login_hint`), `GET /auth/callback`,
  `routers.auth.terminer_la_connexion` (`org=`), the platform identity card (`oidc.per_organisation`), the
  console's sign-in page; story S48-02. Builds on ADR 0024 (two editions) and ADR 0090 (an organisation's id
  is its identity).

## Context

An instance had one identity provider, set in the deployment (`global.oidc`, `CHOREGOS_OIDC_*`) and shown
read-only on *Admin → Platform*. A customer who brings its own SSO (its Entra ID, its Okta, its SAML IdP)
could not be served without the operator federating that provider into the instance's own, by hand.

ADR 0024 puts multi-organisation, and "the enterprise identity surface that only matters with it" (SAML,
SCIM, per-organisation group mapping), in the enterprise edition. The core already let an extension finish
a sign-in for another protocol (`terminer_la_connexion`, public for SAML), but nothing confined what such a
provider vouched for. Once each tenant has its own provider, that is the real risk: a tenant's provider is
configured by the tenant, so it can assert **any** address and **any** group. Without confinement, the
administrator of tenant A's IdP signs in as `ceo@tenant-b.example`, or sends `choregos:tenant-b:org-admins`.

Where a tenant's settings live (a table, a secret store), how its domains are proven, and how a SAML
assertion is verified are product choices of the edition that sells multi-tenancy. What a tenant's provider
may grant is a security property the core must hold whatever the edition does.

## Decision

1. **One seam, a resolver.** An extension declares `resolver(session, SignInHint(org, email_domain)) ->
   TenantSignIn | None` through `tenant_sign_in.declare_sign_in_resolver`. `None` means the instance's
   provider. A `TenantSignIn` names its organisation and **either** an `OidcProvider` the core drives
   (issuer, client id and secret, scopes, the three endpoints for a provider without discovery,
   `require_email_verified`, the groups claim, and a `groups_map` translating the provider's group values, such as
   Entra ID group ids, into the core's group names) **or** a `start_path`, a route of this API where the
   extension starts another protocol (SAML). Without a resolver, nothing changes: the community edition
   keeps one provider (ADR 0024).
2. **Two ways in.** `/auth/login?org=<slug>` (a sign-in link per organisation) and
   `/auth/login?login_hint=<address>` (the console's sign-in page asks for the work address; its domain
   picks the tenant). `login_hint` is passed on to the provider. The organisation chosen at login is
   carried in the signed `state`, and the callback resolves it again: a tenant that lost its provider in
   between is refused.
3. **An address is signed in only by the provider of the tenant that owns its domain.** After a tenant's
   provider vouches for an address, OIDC or through `terminer_la_connexion(org=…)`, the core asks the
   resolver which tenant owns the address's domain and refuses (401) unless it is the same organisation
   (current slug or alias, ADR 0090). An address no tenant owns cannot be signed in by any tenant's
   provider: only by the instance's, which the operator runs. The resolver must map a domain only to a
   tenant that proved it owns it; the core has no other source and trusts that mapping.
4. **A tenant's provider grants roles in its tenant only.** The group mapper (core or extension) runs with
   the tenant as the organisation of bare groups, and any role it yields on another organisation is
   dropped.
5. **A tenant's subject is namespaced**: stored as `tenant:<org id>:<sub>`, because two providers may
   issue the same `sub` (the unique `users.oidc_sub` turned that into a 500).
6. **A tenant's OIDC provider is never guessed.** A discovery document that cannot be read answers 502;
   only the instance's provider keeps its Keycloak fallback (S47-04 removes that one too). An address the
   provider marks `email_verified: false` is refused when `require_email_verified` (the default; Entra ID
   omits the claim and is accepted).
7. **Settings stay with the edition.** The core stores no tenant's settings and has no route to edit
   them; the edition's own administration section (`declarer_une_section_d_administration`) serves that
   screen. *Admin → Platform* says whether such a resolver is declared (`oidc.per_organisation`).

## Consequences

- A customer's own OIDC or SAML provider is served without federating it into the instance's provider,
  once the enterprise edition stores its settings and declares the resolver.
- Members of a tenant whose addresses are on a domain the tenant does not own (contractors, a personal
  address) cannot sign in through that tenant's provider. They use the instance's provider, or the
  tenant proves that domain too.
- The confinement holds for any protocol only if the extension finishes its sign-in with
  `terminer_la_connexion(org=…)`; one that calls `ouvrir_la_session` directly bypasses it. Written in the
  docstring, not enforced.
- The IdP groups stored on the user (`users.groups`, ADR 0052) are still stored as sent, including a group
  naming another organisation; ADR 0052 already ignores a group namespaced for another organisation.
- Roles a tenant's provider stops granting are not revoked at the next sign-in: that is SEC-2, S47-05,
  and applies to every provider alike.

## Alternatives discarded

- **Per-organisation SSO settings stored and edited in the core.** Reopens ADR 0024 (the community edition
  is single-organisation, per-organisation identity is what the enterprise edition sells). Discarded by the
  product owner in favour of the seam.
- **A domain list on `TenantSignIn`.** Two sources of truth for who owns a domain (the list a tenant's
  sign-in carries, and the mapping the resolver uses to route), which can disagree. Asking the resolver the
  same question at login and at the callback keeps one.
- **Trusting the tenant's provider for any address, as the instance's is trusted.** The instance's provider
  is run by the operator; a tenant's is run by a customer. Treating them alike is the takeover this record
  exists to prevent.
- **One sub-domain per tenant.** Needs wildcard DNS and Ingress and a cookie per host; the address domain
  and a link per organisation need neither.
