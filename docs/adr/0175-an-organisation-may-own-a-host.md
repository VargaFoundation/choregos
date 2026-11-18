# 0175 — An organisation may own a host: the host says which organisation, the look follows from the first paint

- **Status**: accepted, 2026-11-17; S74-01 delivers points 1 to 3 and 6 (the seam, the theme, the sign-in
  page's organisation) with the enterprise edition's claims; the sign-in on a tenant host (points 4 and 5) is
  the next step; S74-02 delivers it, S74-03 the links built for an organisation (amendments below)
- **Concerns**: `choregos_api.tenant_host` (`declare_host_resolver`, `resolve_host`, `normalise_host`),
  `GET /api/v1/theme` (`routers/theme.py`), the console's first paint (`served-theme-server.ts`,
  `layout.tsx`, `served-theme.tsx`, `/login`); amends [ADR 0108](0108-each-organisation-signs-in-with-its-own-identity-provider.md)
  (the discarded "one sub-domain per tenant"); builds on [ADR 0064](0064-a-theme-is-data-the-console-applies.md)
  (the theme), [ADR 0090](0090-an-organisation-slug-is-an-alias-its-id-is-its-identity.md) (slug aliases) and
  [ADR 0024](0024-two-editions.md) (what the enterprise edition sells)

## Context

A customer wants `agentic.client.com` to open Choregos on its organisation, in its colours, with its name,
from the first paint, and one day to sign in there. Until now the organisation was chosen by the URL path of
the API (`/api/v1/orgs/{org}/...`) and, in the console, by what the person last chose (the `choregos_org`
cookie and `localStorage`). Nothing read the host: only the internal-API guard looked at `Host`, to refuse.
The look of a first visit was therefore the instance's, whoever the visitor was and whatever address they had
been given.

ADR 0108 discarded "one sub-domain per tenant" for the problem it solved then, a tenant bringing its own
identity provider: "needs wildcard DNS and Ingress and a cookie per host; the address domain and a link per
organisation need neither". That reasoning holds for sign-in routing, and this record does not undo it. It
holds less for a customer that wants its own address: that customer does not need a wildcard, it needs ONE
host it owns, pointing at the instance. What the earlier record feared (a wildcard, a cookie per host) is
what this record keeps out: no wildcard, and a host is served only once its owner proved it.

## Decision

1. **A seam, a host resolver.** An extension declares `resolver(session, host) -> slug | None` (sync or async)
   through `tenant_host.declare_host_resolver`, one per deployment like `declare_sign_in_resolver` (a second
   declaration replaces the first). `host` is a lower-case DNS name, without port or final dot. `None` means
   "not a tenant host": the instance's default. The community edition declares nothing, and every host is
   the instance's.
2. **Only a verified host resolves.** The resolver answers for a host whose owner **proved** it, by a DNS TXT
   challenge on `_choregos-challenge.<host>` carrying a token the instance issued for that organisation: the
   same proof as the mail-domain claims of ADR 0108. A claimed but unproved host, a host whose proof was
   withdrawn, and a host nobody claimed all resolve to `None`. The core trusts the mapping and has no other
   source for it. A host belongs to **one** organisation, instance-wide; the instance's own hosts are never
   claimable. A claim says nothing about DNS routing, TLS or the ingress: those stay the operator's.
3. **The host decides the look, from the first paint.** `GET /api/v1/theme` reads the host (`X-Forwarded-Host`,
   first entry, then `Host`) and serves the organisation's theme **even when `?org=` names another** (the
   console sends the remembered organisation too, and on a tenant host the host wins). The answer carries
   `X-Choregos-Host-Org` and `Vary: Host, X-Forwarded-Host`. The console's server forwards the visitor's host
   to the API in `X-Forwarded-Host`, keeps its memo entry for a tenant host under the host alone (the cookie
   cannot reach it), and does not swap the look after hydration: it is the host's, not the session's. No
   query parameter was added (the OpenAPI contract is unchanged): the headers are the interface.
   Both headers are client-controlled; that is harmless **here**, because the route is public and a host only
   selects a look the route already serves for `?org=`. Nothing that authorises may read them until the
   sign-in step, which must match the host against the verified table and requires the ingress to overwrite
   any client-supplied `X-Forwarded-Host`.
4. **Which host is canonical.** The instance's (`CHOREGOS_PUBLIC_URL`) stays canonical for everything the
   platform stores or emits: OIDC and SAML callbacks, links in notifications and approval e-mails, MCP and A2A
   addresses, webhook URLs, cookies, CORS and origin checks. A tenant host is an **alternative door** to the
   console of one organisation, never the identity of the instance. Making an organisation's own host
   canonical for what concerns it (a per-organisation public URL) is a later, separate decision.
5. **A member of another organisation on a tenant host** (rule fixed now, implemented with the sign-in step).
   The sign-in link of a tenant host implies that organisation (`/login` already does, point 6), so the
   tenant's provider and ADR 0108's confinement apply: the address's domain must be owned by that tenant. A
   person signed in with the instance's provider who is not a member of the host's organisation is refused with
   a page that says so and offers the sign-out; the console never shows another organisation's data under the
   host's brand, and the organisation switcher on a tenant host offers the host's organisation only. A member
   of several organisations, the host's included, lands on the host's.
6. **`/login` on a tenant host implies the organisation.** The root layout hands the host's organisation to the
   sign-in page, which sends `org=<that organisation>` to `/auth/login`, whatever its own `?org=` says.
   Signing in **on** that host (the redirect URI, the session cookie, the origin) is point 5's step, not this one:
   until it ships, the themed sign-in page sends the visitor through the canonical host's flow.
7. **Failure is the instance's look.** A resolver that raises or answers something that is not a slug (the
   core re-checks the shape) is "no organisation", logged: the routes that read it draw the first paint of
   the sign-in page and must not take the console down. A name that is not a DNS name (an address, a name
   without a dot, an overlong or non-ASCII value) never reaches the extension's storage.

## Consequences

- An organisation of the enterprise edition can claim a host, prove it with one TXT record, and its visitors
  see its theme on the first paint of the sign-in page, without a cookie set before.
- The community edition is unchanged: no resolver, the headers are never read for anything else.
- A first visit on a tenant host costs the same single theme fetch as before (memoised for a minute per host).
- Until the sign-in step, no one can sign in **on** a tenant host: that is stated in STATUS, not hidden.
- The extension must follow slug renames (ADR 0090): the resolver answers with the current slug.

## Not decided here (next steps, each its own story)

Sign-in on the host was this list's first item and is the first amendment below (S74-02); the per-organisation
public URL for links, MCP, A2A and notifications is the S74-03 amendment; the chart, ingress and TLS (HTTP-01
issuance is infra#1595) are S74-04, delivered. Still open: SAML assertion consumer service per host; per-host
consent and forge callbacks (see 'Return URLs' in the S74-03 amendment).

## Amendment, S74-02: signing in on a verified host

Points 4 and 5 hold; this fixes how they are enforced. The host that counts is `X-Forwarded-Host` (first
entry), else `Host` (`tenant_host.request_host`), and it counts only once the resolver says it is VERIFIED
(`tenant_host.tenant_door`). The ingress MUST overwrite a client-supplied `X-Forwarded-Host`; the console's
relay already sets it.

1. **OIDC.** On a verified host `/auth/login` implies the host's organisation (its own provider if it has
   one, else the instance's), whatever `?org=` says, and sends `redirect_uri=https://<host>/api/v1/auth/callback`
   (the instance's scheme, no port). The host is signed into the `state` (`h`); the callback runs only on that
   host, which must still be verified, and exchanges the code with the same `redirect_uri`. A sign-in begun on
   one host and answered on another, on an unverified one, or after the claim was withdrawn is refused (401)
   before the code is exchanged. An unverified host, or any spoofed header, behaves as before: the canonical
   callback, no `h`.
2. **Cookies.** The handshake cookie and the session cookie carry no `Domain`: valid on the host that set
   them, no sub-domain, no sibling. A session opened on a tenant host is therefore not valid on the
   canonical one, and the reverse.
3. **Redirect target.** A relative path, or an absolute URL on the instance's origin; on a verified host also
   that host's own origin (same scheme, no port), and an empty target goes to the host's root, not the
   instance's. Another tenant's host, a claimed one, a lookalike, `//x` and `/\x` are refused. A sign-in on
   the instance never follows a tenant host (no valid cookie there).
4. **Another organisation's member** (point 5). After the roles of the sign-in are applied, a person with no
   membership in the host's organisation is refused with 403 and nothing of theirs is stored (the transaction
   ends with the error). The page that says so with a sign-out button is not built: the console shows the API's
   problem detail until a follow-up gives it a page. The organisation switcher restricted to the host's
   organisation is the console's, not done here; the session itself is not confined to one organisation.
5. **Origin and run API guards.** `origin_guard` accepts the Origin of a verified host at runtime, without
   `CHOREGOS_CORS_ORIGINS`: it must equal the host this request was made for, share the instance's scheme,
   carry no port. `internal_api_guard` treats a verified host as public, so `/api/v1/internal/*` is not
   served there. Both ask the resolver through `tenant_host.verified_host_cached` (own session, 30 s memo,
   bounded at 1024 hosts, a failure is "not verified"): a claim proved or withdrawn takes effect within 30 s.
6. **SAML stays canonical.** Its entity id and assertion consumer service derive from `CHOREGOS_API_URL`, one
   per organisation, and an identity provider registers one. On a tenant host a SAML organisation is
   redirected to the canonical start route and signs in on the canonical host (target forced to the
   instance's). A per-host SAML service is a later decision.
7. **Provider-side registration (operator).** Each verified host's callback must be accepted by the provider:
   for the platform Keycloak client `choregos`, add `https://<host>/api/v1/auth/callback` to its valid
   redirect URIs (`platform-realm.json` in the infra repository) and, if used, the host to its web origins; an
   organisation with its own provider (ADR 0108) registers the same URI there. The core does not do it.

## Amendment, S74-04: the chart serves the hosts

The chart carries a host an organisation owns as `global.customHosts` (`templates/custom-hosts.yaml`;
`docs/deployment.md`, "A customer's own host, end to end"). Decisions:

1. **Routing and TLS only.** Per host: the console and the API routed exactly as the canonical host
   (the API's paths, then `/` to the console; a test compares them with the API's own route), and a
   cert-manager `Certificate` (HTTP-01, so one per host, no wildcard) from a configurable namespaced
   Issuer (infra#1595: `choregos-issuer`). The chart never decides which organisation a host belongs to:
   that stays the verified claim read at run time (point 2).
2. **Hosts are also handed to the API as public names** (`CHOREGOS_PUBLIC_HOSTS`), although S74-02 treats a
   VERIFIED host as public at run time. A host the chart routes is reachable from the Internet whether
   its owner has proved it or not (a CNAME is enough), and until the proof the run-time guard does not know
   it: `/api/v1/internal` would be served there. Listing it closes that window, and costs nothing once
   the host is verified. The chart does not add the hosts to CORS origins: an origin is accepted only
   from a verified host.
3. **Three shapes**, because the deployments differ: one Ingress per host with `tls`; NGINX Inc.
   (`nginx.org`) mergeable Ingresses, where every host needs its own master (host and TLS secret, no path)
   and two minions; a Gateway API HTTPRoute per host, whose listener and certificate stay the operator's.
   The operator's annotations cannot change the master's type.
4. **The render refuses** a name that is not a bare lower-case DNS name (wildcard, scheme, port, upper
   case), a name the chart already routes (so a custom host cannot shadow the console, the API, a run
   door or the outposts' door), two hosts with one object name, and an enabled certificate without an
   Issuer.
5. **Not decided by the chart**: DNS, the TXT proof, the Keycloak redirect URI, the Issuer and its policy
   exception, and (NGINX Inc.) the solver Ingress's need to be a minion of the host's master: documented,
   not proved without a cluster.

## Amendment, S74-03: links built for an organisation

Point 4 said "a per-organisation public URL is a later, separate decision": this is it, narrowed. The canonical
`CHOREGOS_PUBLIC_URL` stays valid and stays the address of everything the instance owns; what changes is the
address in a link the platform builds **for one organisation** (a console link in a notification, an e-mail,
Slack, a tracker comment or field, a pull request body; the MCP resource metadata; the A2A card; the device
sign-in page).

1. **A second seam, the reverse.** An extension declares `resolver(session, slug) -> host | None` through
   `tenant_host.declare_org_host_resolver` (sync or async, one per deployment): the ONE host organisation
   `slug` is reached on. The core's `tenant_host.org_base_url(slug)` is `<instance scheme>://<host>` for it,
   else `public_url`. **The core re-verifies the answer**: it keeps the host only if it is a bare DNS name
   (`normalise_host` gives it back unchanged) and the FORWARD resolver, asked now, gives the same organisation
   for it. A claimed-but-unproved host, another organisation's host, a malformed answer and a raising resolver
   all give the instance's address: a link never points to an unverified host, whatever the extension returns.
   With no forward resolver (the community edition) the reverse one is never asked.
2. **Several verified hosts: the oldest verified wins.** The extension MUST answer deterministically; the rule
   of the enterprise edition is "the host verified first (`verifie_le` ascending), ties broken by host name".
   It needs no new column and no choice for the customer to make, and it does not move when a later host is
   added or withdrawn, except when the primary itself is withdrawn (the next oldest takes over). A customer-chosen
   "primary" flag is a later refinement; it would change the extension's query only.
3. **Own session, memo.** The lookup runs in a session of its own over every organisation (the claims are the
   platform's, as for `verified_host_cached`) and is remembered 30 s per slug, refusals included, bounded at
   1024 slugs: a host verified or withdrawn changes the links within 30 s.
4. **Per organisation, switched** (the organisation is known where the link is built): the orchestrator's
   courtesies and tracker writes (finding, approval, status comment and `Run` field, human request, withheld
   pull request body; one reader, `activities.base.console_base`); the A2A door (card `url` and `provider.url`,
   task links, push) and the MCP tool links (`Context.console`); the pending list, conversation confirmation
   links and launch results; the answer to a `/choregos` comment on a tracker; the device sign-in page when the
   request names an organisation.
5. **The door called, switched** (no organisation yet, and the address must equal what the caller typed): the
   MCP protected-resource metadata `resource` and the `resource_metadata` of a 401, and the integrations page's
   `mcp_url`, use the verified host the request came in on (`tenant_host.request_base_url`), else the instance's;
   so does the device page when no organisation is named. The MCP and A2A doors also accept a browser `Origin`
   equal to that verified host, as `origin_guard` does. The **authorisation server stays the instance's identity
   provider**; the token audience is the configured `mcp_oauth_audience`, unchanged by the host.
6. **Instance-level, kept canonical**: OIDC and SAML callbacks and redirect targets (S74-02), the origin and
   internal-API guards, the GitHub App manifest, its webhook and setup page, the MCP gateway's `iss` (stored,
   compared verbatim), the CLI install script, the Slack adapter's `public_url` setting (not used to build a
   link: the link is the message's), the https check of the agent catalogue.
7. **Return URLs: ambiguous, kept canonical on purpose.** The consent flow of a connected account (its start
   page, `redirect_uri` on `CHOREGOS_API_URL`, the page it returns to), the forge link flow, the remote
   credential flow and the GitHub App creation return to the instance's address. The provider knows ONE callback
   (the instance's) and the callback needs a session, which on a tenant host is host-only (S74-02): moving the
   start page or the return to the tenant host would end on a callback without a session, or on a provider that
   refuses an unregistered `redirect_uri`. Making them per host takes, per host and per provider, a registration
   only the operator can make (below) and a handshake that survives the host change: a later story.
8. **What the operator must register.** For a link to a verified host to work for a signed-out person, the S74-02
   registration must exist (Keycloak client `choregos`: `https://<host>/api/v1/auth/callback` among the valid
   redirect URIs, the host among the web origins; an organisation's own IdP registers the same URI). The core
   cannot know it: **verify the host only after registering it**, since from verification on the platform's
   links use it. Nothing to register for the MCP resource (the client's callback is the client's); for a
   connected account, a forge or the GitHub App nothing changes (instance callbacks).
9. **A guard so that the classification does not rot.** `apps/api/tests/test_public_url_call_sites.py` lists
   every file reading `public_url` in the API and the orchestrator with its class (per organisation, instance,
   ambiguous) and the number of reads: a new read fails until it is classified.

## Alternatives discarded

- **A wildcard sub-domain per tenant** (`<slug>.choregos.example`). Still discarded for ADR 0108's reasons:
  wildcard DNS and certificate, and it does not serve a customer's own domain.
- **A `?host=` query parameter on `/theme`.** A contract change for no gain: the console's server already
  forwards the visitor's host as a header, and the look is drawn on the server, not fetched by the browser.
- **The host as a hint, the cookie wins.** The cookie is the visitor's last choice; on a customer's own
  address that would show the instance's or another tenant's look to the customer's staff.
- **Trusting a claimed host without a proof.** Anyone could claim `login.bank.example` and serve it a themed
  sign-in page on the instance.
- **Core-side storage of hosts.** A table of claims per organisation is what the enterprise edition sells
  (ADR 0024); the core holds the seam and the guarantees around it.
