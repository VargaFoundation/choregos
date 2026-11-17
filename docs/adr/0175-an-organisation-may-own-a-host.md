# 0175 — An organisation may own a host: the host says which organisation, the look follows from the first paint

- **Status**: accepted, 2026-11-17; S74-01 delivers points 1 to 3 and 6 (the seam, the theme, the sign-in
  page's organisation) with the enterprise edition's claims; the sign-in on a tenant host (points 4 and 5) is
  the next step and is not delivered
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

Sign-in on the host: OIDC `redirect_uri` per host, host-only session cookies, the origin and CORS allowlist
at runtime (`CHOREGOS_PUBLIC_HOSTS` grows with claims), Keycloak redirect URIs, SAML assertion consumer
service per host. A per-organisation public URL for links, MCP, A2A and notifications. The chart, ingress and
TLS (HTTP-01 issuance is infra#1595).

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
