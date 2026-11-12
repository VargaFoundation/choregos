# 0160 — A change of identity provider re-links accounts in a time-boxed window

- **Status**: proposed, 2026-11-12
- **Concerns**: `routers.auth` (`_ensure_oidc_user`, `_relink_in_window`), `services.identity_relink`,
  `users.oidc_issuer`, `CHOREGOS_OIDC_RELINK_UNTIL` (chart `choregos-api.oidcRelinkUntil`),
  `GET /platform/identity/relink`, `choregos admin identity relink`; story S70-20. Amends
  [ADR 0100](0100-console-sign-in-binds-to-the-provider-subject.md) §2 ("Re-link", on the instance).

## Context

A console account is bound to its provider's subject (`users.oidc_sub`, unique; ADR 0100), and a subject
only means something at the issuer that gave it (OpenID Connect Core 1.0 §2: "locally unique and never
reassigned identifier within the Issuer"). When an instance moves to another identity provider, every
person gets a new subject: without a re-link, each first sign-in through the new provider finds no account
for its subject, and either is refused ("this e-mail belongs to another identity") or, for an address that
differs, creates an empty account. The old account, with its memberships, the runs it started, the
decisions and approvals it recorded and the audit trail, is orphaned.

ADR 0100 had a switch for it, `oidc_relink_verified_email`: while on, an account bound to another subject
was re-linked when the provider asserted the address with `email_verified: true`. Found on `origin/main`:

- **It had no end.** A switch left on keeps a take-over path open: any account of the provider with a
  verified copy of a person's address takes that person's account, months later.
- **It moved an account more than once.** Two identities of the new provider with one verified address
  took the account in turn, each sign-in re-binding it, because nothing recorded that the account already
  belonged to the new provider.
- **It matched the address exactly.** A provider that writes `jo.martin@example.com` where the account
  says `Jo.Martin@example.com` created a second, empty account instead of moving the first; and nothing
  refused two accounts that differ only by letter case.
- **It re-bound a tenant's member** (`tenant:` subject, ADR 0108) to the instance's provider.
- **Nothing said beforehand what it would do**: no list of the accounts it would move, nor of those it
  would refuse.

## Decision

1. **The issuer is recorded beside the subject.** `users.oidc_issuer` (nullable) holds the issuer, as
   configured without its trailing slash, of the subject the account is bound to. Every sign-in through an
   OIDC provider writes it: the instance's, or a tenant's own. An account bound before this change has a
   null issuer until its next sign-in.
2. **A re-link is a window with an end.** `CHOREGOS_OIDC_RELINK_UNTIL` (chart
   `choregos-api.oidcRelinkUntil`) is an instant; until then the window is open, afterwards it is closed
   and the sign-in refuses as it did before. It is at most 31 days ahead, or the API refuses to start: a
   window is an event, not a mode, and a longer one is a new decision, written again. A value without a
   time zone is UTC. `CHOREGOS_OIDC_RELINK_VERIFIED_EMAIL=true` (`choregos-api.oidcRelinkVerifiedEmail`)
   refuses to start and fails the render, naming the replacement.
3. **What the window moves.** A sign-in through the instance's provider whose subject is unknown moves an
   account when, and only when, the window is open, the provider asserts the address with
   `email_verified: true` (omitted or any other value is not enough), and exactly **one** account is bound
   to another issuer's subject, or to a subject whose issuer was never recorded, with that address, letter
   case aside. The account keeps its id, so everything that names it follows; its subject and issuer
   become the provider's, its address follows the provider's unless another account holds it. Audited
   `user.relink` with `previous_sub`, `previous_issuer`, `issuer` and `window_until`. One transaction: the
   sign-in's. A second sign-in finds the subject: nothing more is moved.
4. **What the window refuses.** An account already bound to a subject of this issuer (an account is never
   moved twice), two candidate accounts for one address (both refused, logged `oidc.relink_refused`, until
   an administrator resolves them), a tenant's member (its provider re-links it, ADR 0108) and a machine
   account (ADR 0131). Outside a window, an account bound to another subject is refused, as before. An
   account with no subject, or a development one, is linked by address as ADR 0100 says, window or not.
5. **A plan, before and during the window.** `GET /platform/identity/relink?issuer=<new issuer>` (platform
   administrator; `choregos admin identity relink --issuer …`) is a dry run that sorts every account:
   `pending` (moved at its first sign-in), `ambiguous` (refused, with the addresses it collides with),
   `current` (already the issuer's), `unbound` (no subject: linked by address at its first sign-in, window
   or not) and `not_concerned` (development subjects, tenants' members, machine accounts), and says whether
   the window is open and whether it applies to that issuer. The plan and the sign-in read the same two
   functions (`concerned`, `relink_candidates`): a test plays the plan's promises through the sign-in.
   *Admin → Identity* shows the window ("open until …", "closed since …", "none").

## Considered and rejected: a batch re-link by the administrator

The story asked for an administrator's operation that re-links every account at once: dry run by default,
then an apply mode, idempotent, one transaction per account, audited, refusing ambiguous matches. It was
weighed against the window and rejected, for three reasons:

- **The new subject is usually unknown before the person's first sign-in.** A batch needs, for every
  person, the new provider's `sub`, address and `email_verified`. A provider that federates the old one
  (identity brokering) creates its account at the first sign-in through it, so the subject does not exist
  yet. Microsoft Entra ID's `sub` is pairwise, specific to the application, and is not the directory's
  object id that an export lists. Most providers have no standard export of subjects at all, so the core
  would read a provider-specific API or a file the operator writes.
- **A file is not a proof.** In a batch, the binding rests on a list typed or exported by an operator; one
  wrong line binds a person to another person's account, with their history and their approvals. In the
  window, the binding rests on the provider's own answer, at the moment the person proves they hold the
  account at the provider.
- **The window keeps what the batch promised.** A dry run before (the plan), one transaction per account
  (the sign-in's), idempotence (a second sign-in finds the subject), an audit entry per re-link, and the
  ambiguous refused; plus an end. It takes no lock on `users` and has no big-bang step, which matters on a
  large instance.

Its cost: a person who does not sign in during the window stays bound to the old subject. The plan lists
them (`pending`); the operator opens another window for them, or lets them be refused, which is the safe
default.

Also rejected:

- **Binding identity to `(issuer, sub)` everywhere in this change**: the subject stays unique on its own
  (ADR 0100) and a returning subject keeps its account whatever issuer gives it. Recording the issuer is
  the first step; refusing a known subject that another issuer presents is a separate decision, with its
  own upgrade path for accounts whose issuer was never recorded.
- **Matching on a claim that carries the previous subject** (a broker can expose the federated identity's
  id): stronger than the address, but provider-specific, and not what this change needs to stay generic.
- **An instance setting in the database, opened from the console**: the instance's sign-in configuration
  lives in the deployment's settings (`GET /platform/identity` shows it read-only), and the issuer change
  that needs the window is itself a deployment change, reviewed in the same place.

## Consequences

- An operator who changes the instance's provider sets the new issuer and a window in the same
  deployment, after reading the plan; the procedure is in `docs/deployment.md`, "Changing the identity
  provider".
- A deployment that set `choregos-api.oidcRelinkVerifiedEmail: true` fails the render, and an API started
  with `CHOREGOS_OIDC_RELINK_VERIFIED_EMAIL=true` refuses to start, both naming `oidcRelinkUntil`.
- A migration adds `users.oidc_issuer`; no row is rewritten.
- Not decided here: what to do with an account whose address changed at the new provider (the window
  cannot see it; a person signing in with another address gets a new account, and the old one stays
  `pending`), nor a provider that never asserts `email_verified` (Entra ID omits it by default): such a
  provider cannot be the target of a window.
