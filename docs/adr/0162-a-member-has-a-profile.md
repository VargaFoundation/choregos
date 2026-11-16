# 0162 — A member has a profile

- **Status**: proposed, 2026-11-14; S66-42 implements it
- **Concerns**: `apps/api` (`users` columns, `user_avatars` table, `routers/member_profiles.py`,
  `services/member_profiles.py`, `services/avatars.py`, the sign-in paths of `routers/auth.py`,
  `tenant_sign_in.OidcProvider`), `packages/contracts`, `apps/web` (Administration > Members, a member's
  page, My account)
- **Builds on**: [0130](0130-a-browser-session-is-recorded-and-its-owner-ends-it.md) (sessions),
  [0153](0153-a-human-step-may-be-addressed-to-a-person.md) (who a request waits on),
  [0160](0160-groups-belong-to-the-organisation.md) (groups), [0161](0161-a-member-links-their-forge-accounts.md)
  (linked accounts)

## Context

The console lists an organisation's members as e-mail addresses with a role. Everything else known about a
person is scattered: their groups on the Groups pages, their linked accounts on a card of the members page,
the requests waiting on them inside each work item, their sessions and tokens on their own My account only,
what they did in the audit log. An administrator who wants to know who `jdoe@…` is, what waits on them and
what they decided has to visit six screens; a member who wants to know who a colleague is cannot see more
than their address.

The `users` row holds only the e-mail, a display name, the last sign-in and the identity provider's groups.
Identity providers send more at sign-in (a given and a family name, often a job title and a department) and
the platform drops it. And a person has no face: the console's content security policy is `img-src 'self'`,
so neither a provider's `picture` URL nor a forge avatar can be shown.

## Decision

1. **One page per member, one read.** `GET /orgs/{org}/members/{user_id}/profile` returns the profile, shaped
   by who reads it. The console shows it at `/admin/members/<user_id>`, reached from the members list; the
   person reaches their own from My account.
2. **Who sees what.**
   - Any member of the organisation (`member:read` in it) reads the **basic card** of another member: names,
     job title, department, e-mail, photo, status, organisation and project roles, groups. The members list
     already shows them the address and the roles; the card adds what the identity provider says of the
     person. Nothing about their activity.
   - An administrator (`member:manage` in the organisation) and **the person themself** read the **full
     profile**: how the account came, the last sign-in, the open sessions count and last activity, and their
     work (point 5).
   - An administrator only reads the **administration** section: the number of API tokens that have not
     expired (never a token), and the organisation's recent audit events where the person is the actor or the
     target. The person sees their own tokens on My account already.
   - A caller outside the organisation gets 403 before any lookup, as `GET /orgs/{org}/members` does. A person
     who is not a member is 404.
   - **A former member** (no role in the organisation or its projects any more) is read by an administrator
     only, when the organisation's audit log names them as actor or target: status `former`, no groups,
     sessions or photo (they are not the organisation's any more), but their decisions and runs in it, and
     the audit. Under the RLS of `users` such a person is invisible to the organisation; the row is read for
     the whole instance, for them alone, once the audit log has proved they were here. Anyone else gets 404.
3. **What the identity provider says is stored at each sign-in.** `users.given_name`, `family_name`,
   `job_title`, `department` (nullable), from the standard OIDC claims `given_name` and `family_name` and from
   `job_title` (or `jobTitle`, Entra ID's optional claim) and `department`. A tenant's provider names its own
   job title and department claims (`OidcProvider.job_title_claim`, `department_claim`). A sign-in brought by
   an extension (SAML) maps its attributes onto the same names through `terminer_la_connexion(profile=…)`. A
   claim a sign-in carries replaces the stored value; a claim it does not carry leaves it: a provider that
   stops sending a job title does not erase it. The values are strings, trimmed, at most 200 characters.
4. **A photo is one the person uploaded, served same-origin.** `PUT /me/avatar` takes one PNG, JPEG or WebP
   file (multipart `file`) of at most 2 MiB and 32 to 4096 pixels a side. It is checked by its first bytes
   (415 for any other type, whatever its declared content type), decoded by Pillow restricted to the format
   those bytes announce (422 when it does not decode or its sides are out of bounds), turned upright by its
   EXIF orientation, cropped to a centred square, scaled to at most 512 pixels and **re-encoded as WebP**: no
   metadata (a phone's position) and nothing of the original file survives, so a file that is also something
   else is never served back. The bytes are stored in the database (`user_avatars`, one row per person, a
   few dozen kilobytes), as a knowledge document's are, not in the object store, which a deployment without
   file outputs does not configure. `GET /users/{user_id}/avatar` serves them to the person and to the
   members of an organisation they share (404 for anyone else, as for no photo), with `nosniff`, a
   `default-src 'none'; sandbox` policy, a private cache and an `ETag`; the console's `avatar_url` carries the
   digest, so a new photo is a new address. `user_avatars` is under forced RLS with the policy of
   `api_tokens`: read by the person and by whoever sees them, written by the person. **A `picture` claim is
   never fetched**: the platform makes no outbound call to an address a provider chose, and it is not stored.
   Without a photo, the console draws the person's initials.
5. **The work section** (full profile), in the organisation's team projects the caller reads — a personal
   space is never shown (ADR 0134):
   - **the human requests waiting on them**: undecided requests of open work items addressed to them, or to a
     group they are in (an organisation group, or the identity provider's as of their last sign-in), by the
     rules a decision checks (ADR 0153): a group answers a request that also names a person only once its SLA
     has passed. Each says `via` person or group;
   - **their recent decisions** on work items (`human_requests.decided_by`);
   - **the runs they started**: the runs of the work items whose initiator (`initiated_by`, ADR 0077) is them
     through the console, the API or the MCP door, with each run's cost;
   - **their spend this month**: what the ledger books to them in the organisation since the first of the
     month. The platform measures per-person spend for assistant conversations and tasks only (the ledger's
     `user_id`, S63-15, used by the month caps); those lines sit in the person's personal space, so the sum is
     made for the whole instance and returns a total, never a row, as the month caps do. **A run is not
     attributed to the person who started it in the ledger**, so the spend does not include runs; each run
     shows its own cost in the list.
6. **How the account came** (full profile): `identity_provider` when the person signed in through one (a bound
   subject, or a membership a sign-in created); `provisioned` when an identity provider pushed them into a
   group of the organisation (SCIM) before any such sign-in; `manual` when an administrator invited them, or a
   development sign-in.

## Consequences

- One screen answers "who is this person, what may they do, what waits on them". The members list links to it.
- Every member sees a colleague's job title, department and photo: what a provider sends is shown to the
  organisation. A provider that should not share a job title must not send it.
- A photo costs a few dozen kilobytes of database per person, and one decode of at most 4096×4096 pixels per
  upload, bounded before decoding.
- The profile of a former member exists only as long as the audit log keeps a row that names them.
- `pillow` becomes a direct dependency of `choregos-api`; it was already shipped through MarkItDown.

## Not decided here

- Editing the name, job title or department in the console: the identity provider stays the source.
- A photo pushed by a provisioning protocol, or fetched from a provider through a proxy the platform controls.
- The forge avatars of linked accounts (ADR 0161 still draws a monogram).
- Attributing a run's spend to the person who started it, and a per-person budget.
- A directory of people across the organisation (search, filters by group or department).
