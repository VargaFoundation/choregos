# 0161 — A member links their forge accounts, and proves them

- **Status**: proposed, 2026-11-12; S66-41 implements it
- **Concerns**: `apps/api` (`linked_accounts` table, `routers/linked_accounts.py`,
  `services/linked_accounts.py`, the signed-comment path of `routers/webhooks.py`),
  `packages/adapters` (`forge_accounts`, the `forge_account` connector kind), `packages/contracts`,
  `apps/web` (My account, Administration > Members, `components/accounts/`), the setup procedures
- **Amends**: [0102](0102-a-tracker-comment-carries-no-decision.md) (who a signed comment's author is)

## Context

ADR 0102 (S47-14) let an organisation administrator link a member to a GitHub numeric user id, in
`tracker_identities`, so that a `/choregos pause|resume|stop` comment in a signed GitHub delivery runs with
that member's rights. The link is asserted: nothing proves the member owns the account, and an
administrator who types the wrong id hands someone else's comments the member's rights. It is GitHub only,
and only github.com.

Members work on more than one forge: github.com, gitlab.com or a self-managed GitLab, and self-hosted
Forgejo instances whose users sign in through the same corporate identity provider as the platform. Later
stories need a member's account on any of them (a profile page, a review requested from the right person,
a commit attributed to a member), and need to know whether the member proved it.

## Decision

1. **A linked account is a provider and an instance.** `tracker_identities` becomes `linked_accounts`:
   `provider` (`github`, `gitlab`, `forgejo`), `base_url` (the instance, normalised: `https://github.com`,
   `https://gitlab.example`), `external_id` (the forge's stable numeric user id), `login`, `avatar_url`,
   `profile_url`, `verified`, `verified_at`, `connector` (whose OAuth application proved it), `created_by`.
   One account per member and instance, one member per account on an instance, in an organisation
   (`(org_id, user_id, provider, base_url)` and `(org_id, provider, base_url, external_id)` unique). Same
   forced row-level security as every organisation row. The migration copies every row as `github` at
   `https://github.com`, **not verified**, with its id, author and timestamps.
2. **Only `external_id` is ever matched** (ADR 0102 unchanged): a login, an avatar or a profile URL is shown,
   never compared. The same numeric id on another instance is another account.
3. **A member proves an account by signing in to the forge.** From My account, the member starts the link
   (`POST /me/linked-accounts/{provider}/start?org=&instance=`); the platform answers the forge's
   authorization URL: authorization code with **PKCE `S256`**, the provider's narrowest scope that reads
   the user (`read:user` on GitHub and Forgejo, `read_user` on GitLab), and a **`state` signed for the
   member, their browser session, the organisation, the instance and a nonce**. The PKCE verifier waits in
   an HTTP-only cookie bound to the same nonce, whose path is the callback's only. The forge sends the
   browser to `GET /auth/linked-accounts/callback`, which refuses any state that is not this member's, this
   session's and this handshake's before calling the forge; then exchanges the code (client secret and
   verifier), reads the forge's user (`/user`: numeric id, login, avatar, profile), and records the link
   `verified`. The **access token is used for that one read and dropped**: nothing stores it, no route
   returns it. The callback always redirects to My account with `?linked=` or `?link_error=`.
4. **A forge account is one member's.** An account already linked to another member (proved or declared)
   is refused at the callback (`taken`) and at a declaration (409). A member who proves the account an
   administrator declared for them turns that same row verified.
5. **An administrator may still declare** a member's account by numeric id and login
   (`POST /orgs/{org}/members/{user_id}/linked-accounts`), on any provider and instance: it is stored and
   shown **not verified**. A declaration never downgrades a proof: declaring the account a member already
   proved leaves it verified. The member reads their own links; an administrator reads and removes anyone's;
   the member removes their own. Every write is audited (`linked_account.verify`, `.declare`, `.delete`).
6. **The OAuth applications are organisation connectors** of a new kind, `forge_account`, with three types:
   `github-oauth` (`base_url` defaults to `https://github.com`; a GitHub Enterprise Server by its address),
   `gitlab-oauth` (`https://gitlab.com` or a self-managed instance) and `forgejo-oauth` (self-hosted only:
   `base_url` required). Each holds `base_url` and `client_id` in its configuration and `client_secret` as
   a secret field. No operation: an agent calls nothing through it.
7. **GitHub sign-in is an OAuth application the organisation registers**, not the platform's GitHub App.
   An OAuth App with `read:user` is the documented path; the client id and secret of a GitHub App are
   accepted as well (GitHub's user authorization of an App uses the same endpoints), provided that App has
   no repository or organisation permission. The platform's own GitHub App (ADR 0073) is not reused: its
   user token would carry the intersection of its repository permissions and the member's, which the
   platform has no use for, and its client secret would leave the platform settings for a second purpose.
8. **Calls to a forge go through the guarded HTTP client** under the organisation's egress allowlist; the
   connector type declares its hosts (`github.com` and `api.github.com` for github.com, the instance's host
   otherwise), so the declaration opens them like any connector's (`allow_egress`).
9. **What a signed comment honours does not change in this story.** The signed-comment path reads the
   member's github.com link, declared or verified, exactly as it read `tracker_identities`. Requiring
   `verified` there is a later switch, once members have had the time to link their accounts.
10. **The S47-14 routes stay, deprecated**: `/orgs/{org}/members/{user_id}/tracker-identities[/github]` read
    and write the member's github.com link (declared). The console uses the new routes.

### Why reuse the connector framework rather than a table of its own

An OAuth application is a credential the organisation holds for an outside service, which is what an
organisation connector is. Reusing it brings, with no new code: the sealed secret store and its rotation
(ADR 0058: `secret_values`, never returned, versions audited), the egress gate at declaration and its
allowlist at call time (S39-22), the network checks of a connector's configuration, the console's
declaration form drawn from the type's schema, the setup procedures, the connector icons, and the audit of
who declared or edited it. A dedicated table would have had to rebuild each of these. What does not fit is
small: the kind has no operation and no project uses it, so its availability to projects means nothing; it
is listed under its own family, **Member accounts**.

## Consequences

- Members prove their own accounts; an administrator's mistake no longer has to be the only source.
  The console shows Verified and Declared side by side, so the difference is visible before it is enforced.
- The console does not load avatars from a forge: its content security policy allows images from the
  platform only. It draws a monogram from the login; `avatar_url` is kept for a later proxy.
- A member linked on several organisations' instances holds a row per organisation: an account proved in one
  organisation is not shared with another.
- The forge's token is never refreshed, revoked or reused: the link records who the person was at the forge
  at that instant. An account deleted or renamed at the forge keeps its numeric id here until the member or
  an administrator removes the link.
- A GitHub Enterprise or GitLab link is not honoured by the signed-comment path, which serves github.com
  deliveries only, as before.

## Not decided here

- Requiring `verified` for signed comments (a switch per organisation, or for all).
- A member's profile page that shows their linked accounts to other members.
- Using a linked account elsewhere: requesting a review from a member's account, attributing a commit,
  matching a GitLab or Forgejo comment once their deliveries are signed per source.
