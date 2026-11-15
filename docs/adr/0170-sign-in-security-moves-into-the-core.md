# 0170 — Re-authentication to approve and the revocation of sessions are served by the core; SAML follows

- **Status**: accepted, 2026-11-15 (decision D5 of the 2026-11 offer, taken by the product owner on
  2026-11-15); S70-22 implements the first two functions, SAML is a later story
- **Concerns**: `approval_freshness.py`, `extensions.check_gesture`, `extensions.IDENTITY_FEATURES`,
  `browser_sessions.revoke_user_sessions`, `routers/org_security.py` (`GET|PUT /orgs/{org}/security`,
  `POST /orgs/{org}/members/{email}/sessions/revocation`); amends [ADR 0024](0024-two-editions.md)
  (what the enterprise edition sells) and builds on [ADR 0130](0130-a-browser-session-is-recorded-and-its-owner-ends-it.md),
  [ADR 0108](0108-each-organisation-signs-in-with-its-own-identity-provider.md)

## Context

[ADR 0024](0024-two-editions.md) sold "SAML, SCIM and deprovisioning" in the enterprise edition (EE).
That put three identity functions behind the licence: SAML for an organisation, a recent
authentication to approve, and the server-side revocation of sessions. The core already carried their
seams (`declare_session_validation`, the gesture checks, `terminer_la_connexion`, ADR 0108) and part of
the substance (`freshness.py`, the per-gesture `step_up_minutes` of policies, the recorded sessions of
ADR 0130), but the organisation-wide demand for a fresh authentication and the administrator's
revocation lived only in the EE.

Decision D5 moves SAML, re-authentication and revocation into the Apache-2.0 core: an organisation
that signs in and approves should not need a licence for that, and the core's own documentation
already presents re-authentication as part of approving. SCIM, and the de-provisioning it drives, stay
in the EE.

## Decision

1. **Re-authentication to approve is the core's.** An organisation sets `approval_freshness_s`
   (seconds, at least 60, absent = no requirement) in its `settings`, through `GET|PUT
   /orgs/{org}/security` (its `org_admin` only, audited `org.security`). `check_gesture` plays the core's
   own check BEFORE any extension's, for the gestures that approve: a human decision `approve` or
   `scope_change`, `release.approve`, and an agent release to `stable` (promote, rollback, publish) or
   its approval. A stale or unknown authentication date is a 401 pointing to
   `GET /api/v1/auth/login?reauth=1`, as the EE answered. Answering a question is not approving. The
   per-gesture `step_up_minutes` of ADR 0071 and the action policies are unchanged and independent.
2. **Revocation of sessions is the core's.** `POST /orgs/{org}/members/{email}/sessions/revocation`
   (its `org_admin`, a member of that organisation only, audited `user.sessions.revoke`) ends every
   session of the person by the machinery of ADR 0130 (`revoke_user_sessions`: recorded sessions ended,
   older cookies fall below `users.sessions_not_before`). One mechanism: the EE's own
   `ee_revocations` table is no longer the source of truth where the core serves this.
   A person's sessions are the person's, not an organisation's: revoking cuts them everywhere. In a
   multi-organisation instance an organisation's administrator can therefore end the sessions of a
   member who belongs to other organisations too; that is the same reach as the person's membership, and
   the platform-wide route stays the EE's.
3. **The core announces what it serves.** `extensions.IDENTITY_FEATURES` lists `approval_freshness` and
   `session_revocation`. An extension detects them by that announcement, never by version, and does not
   serve what the core serves (a route overlapping a core route stops the start, ADR 0024's seam rules).
4. **SAML is decided, not delivered here.** SAML (an IdP per organisation, signed-assertion
   verification, replay protection, domain ownership) moves in a later story; until then the core keeps
   the `tenant_sign_in` seam (ADR 0108) and the EE keeps serving SAML. The reason it is not in this
   change: it adds a XML-signature dependency to the core's image and a security-critical verifier that
   deserves its own review and test matrix, not a slot in a move of two small functions.
5. **No relicensing, no removal without a bridge.** The code is written in the core under Apache-2.0
   with SPDX headers; it is not copied from the EE's text. The EE tolerates both cores, then drops its own
   copy once its floor passes the first core that announces the functions (two steps, in the EE's PR).

## Consequences

- A community installation can require a fresh authentication to approve and end a member's sessions,
  with no extension.
- The EE's platform-wide revocation route and SCIM de-provisioning remain, and end sessions through the
  core's mechanism on a core that announces `session_revocation`.
- An organisation that had set `fraicheur_approbation_s` in the EE keeps it: the EE migration copies it
  to `organizations.settings.approval_freshness_s` (the value, not a reference).
- Not covered: SAML (above), an administrator's view of another person's sessions (ADR 0130), a console
  screen for these settings in the core (the EE's section keeps rendering them; the core ships the API),
  and a platform-scope revocation by e-mail without an organisation.

## Alternatives discarded

- *Move the EE's `ee_revocations` table into the core.* ADR 0130 already records sessions and cuts them
  server-side; a second mechanism with a second table would have two answers to "is this session valid".
- *A new column on `organizations`.* `settings` already carries per-organisation options; a column buys
  nothing here and a migration on a table under RLS.
- *Deliver SAML in the same change.* See Decision 4.
