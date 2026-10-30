# 0100 — Console sign-in binds to the provider's subject

- **Status**: accepted, 2026-10-30
- **Concerns**: `GET /auth/login`, `GET /auth/callback`, `routers.auth` (`oidc_endpoints`, `_discover`,
  `_ensure_oidc_user`), `CHOREGOS_OIDC_*_ENDPOINT`, `CHOREGOS_OIDC_REQUIRE_EMAIL_VERIFIED`,
  `choregos-api.oidcEndpoints`; story S47-04. Builds on [ADR 0108](0108-each-organisation-signs-in-with-its-own-identity-provider.md).

## Context

Relation to ADR 0108: that ADR lets an organisation bring its own provider (`OidcProvider`, with its own
three endpoints and `require_email_verified`). This one hardens the sign-in itself, and applies to **both**
providers, the instance's (`CHOREGOS_OIDC_*`) and a tenant's. It reuses what ADR 0108 introduced (the
explicit endpoints, `require_email_verified`, the `tenant:<org id>:<sub>` subject) instead of a second set.

Findings on `origin/main` before this change:

- **Discovery fell back on a guess.** When the discovery document could not be read, the instance's
  provider was assumed to follow Keycloak's layout, and that guess was cached for the life of the process:
  one network hiccup at the first sign-in broke every later one on a provider that is not Keycloak, without
  saying so. The document's `issuer` was never compared with the configured one.
- **An e-mail alone took over an account.** The console looked a user up by e-mail only. A provider
  account with a victim's address and `email_verified: false`, or a re-assigned address, signed in as the
  victim. `users.oidc_sub` is unique, so the opposite case (a returning subject whose e-mail changed) inserted
  a second row and failed with a 500.
- **The `nonce` was signed but never sent**, and the ID token's `iss`, `aud` and `sub` were never read.

## Decision

1. **Discovery is mandatory and its issuer is checked.** The document must be HTTP 200, a JSON object,
   name the configured issuer (trailing slash ignored) and give the three endpoints. Otherwise the sign-in
   answers 503 naming the cause and the way out. Only a success is cached. The only alternative is the three
   endpoints set explicitly (all three or none), for a provider that publishes no discovery document. No
   Keycloak layout is guessed.
2. **Identity is `sub`.** A user with that subject is the account, whatever its e-mail became (the e-mail
   follows unless another account holds it, or the provider calls the new address unverified). Otherwise an
   account with that e-mail is linked only if it is unbound (a development `dev|` subject counts as unbound)
   and the provider does not say `email_verified: false` (`require_email_verified`, default true; only an
   explicit false refuses, since Entra omits the claim). Otherwise a new account is created, never for an
   address the provider calls unverified. A tenant's subject is namespaced `tenant:<org id>:<sub>`
   (ADR 0108), and the instance provider cannot issue a subject starting `dev|` or `tenant:`.
   **Re-link.** An account bound to another subject is refused, with two exceptions, both audited as
   `user.relink`:
   - a **tenant's** OIDC provider may re-link an address whose domain belongs to that tenant (the proof
     ADR 0108 §3 already requires): an organisation that switches to its own provider keeps its members;
   - on the **instance**, `oidc_relink_verified_email` (`CHOREGOS_OIDC_RELINK_VERIFIED_EMAIL`, chart
     `choregos-api.oidcRelinkVerifiedEmail`, default false) lets the provider re-link an account whose
     address it asserts with `email_verified: true`. It is a temporary switch for an identity provider
     migration (every account's subject changes): turn it on, let people sign in, turn it off. A provider
     that omits the claim is not enough.
   The extension path (`terminer_la_connexion`) keeps its previous identity handling and never re-binds.
3. **The ID token's claims are checked, its signature is not.** `iss` equals the issuer, `aud` contains the
   client id, `nonce` (sent in the login redirect) equals the handshake's when present, and its `sub` equals
   userinfo's. The token comes straight from the token endpoint over TLS in the code exchange, which OpenID
   Connect Core 1.0 §3.1.3.7 allows the client to trust without verifying the signature. Identity itself is
   read from userinfo, fetched with the access token.

## Consequences

- A deployment whose discovery document names another issuer than `global.oidc.issuer` now fails closed
  (503). Fix the issuer, or set `choregos-api.oidcEndpoints`.
- A tenant's discovery failure answers 503 instead of 502.
- The development login and `terminer_la_connexion` (an extension's protocol that carries its own subject)
  keep their identity handling.

- **Caveat: rows that already exist.** Accounts created before this change carry whatever `oidc_sub` the
  old code stored. Nothing is migrated: an instance whose provider issues the same subjects keeps working;
  one whose subjects changed needs the migration switch above (or its users are refused).

## Rejected

- **Verifying the ID token's signature with the provider's JWKS for the console.** It adds a key fetch, a
  cache and a rotation path to guard a token that arrives over the authenticated TLS channel the spec
  already relies on, and the identity comes from userinfo anyway. The claim checks above close the swapped
  token and replay cases a signature would. Revisit if the console ever accepts an ID token from the browser.
- **Keeping the Keycloak fallback behind a flag.** The explicit endpoints serve every provider without
  discovery, Keycloak included, without a silent default.
