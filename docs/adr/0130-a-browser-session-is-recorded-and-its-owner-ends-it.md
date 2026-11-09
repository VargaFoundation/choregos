# 0130 — A browser session is recorded, and its owner ends it

- **Status**: accepted, 2026-11-09
- **Concerns**: `user_sessions`, `users.sessions_not_before`, `browser_sessions.py`, `deps.current_principal`,
  `routers/auth.ouvrir_la_session` and `logout`, `routers/my_sessions.py` (`/me/sessions`), the My account
  page; builds on the signed session cookie (`security.sign_session`) and leaves the enterprise edition's
  revocation (`declare_session_validation`) as it is

## Context

A console session was a signed cookie and nothing else: `sub`, `iat`, `exp`, an HMAC. The API kept no
record of it. Three things followed:

- nobody could see where they were signed in: the My account page had nothing to list;
- nobody could end a session but the browser holding it. Signing out cleared the cookie in that browser,
  and a copy of the cookie (another device left signed in, a stolen cookie) stayed valid until its
  `exp`, eight hours later;
- the enterprise edition's revocation cuts ALL of a person's sessions older than an instant, on an
  administrator's request: a platform gesture, not the person's own, and never "all but this one".

## Decision

1. **A session is a row.** Each sign-in, whatever its protocol (OIDC, development sign-in, an extension's
   SAML through `terminer_la_connexion`), goes through `ouvrir_la_session`, which writes a `user_sessions`
   row and puts its id in the cookie as `sid`. The row holds the channel, the expiry (the cookie's
   `exp`), the last activity, the `User-Agent` and the client address of the last request
   (`rate_limit.client_address`, the trusted proxy's view; an IPv6 client as its /64), and `revoked_at`.
2. **The cookie stays the proof; the row says whether it still holds.** The signature and `exp` are
   checked first, as before. A cookie whose `sid` names no row, a row of another user, an ended row or
   an expired one is refused (401, "this session was ended: sign in again"), on every replica, with no
   cache: one indexed read by primary key per cookie request.
3. **Activity is written at most once a minute** per session (`TOUCH_EVERY`), or when the browser or
   the address changes: not one write per request.
4. **The owner ends their sessions**: `GET /me/sessions` lists those that still hold, the current one
   marked; `DELETE /me/sessions/{id}` ends one; `POST /me/sessions/end-others` ends all but the one the
   request came with. Each gesture is audited (`session.end`, `session.end_others`). Signing out
   (`POST /auth/logout`) now ends the session on the server too: a copy of the cookie opens nothing.
5. **Cookies issued before this decision** carry no `sid`. They hold until their `exp` (eight hours at
   most), unlisted, unless their owner ends their other sessions: `users.sessions_not_before` is then set,
   and a cookie without `sid` issued before it is refused. When the request that ends the others itself
   came with such a cookie, its browser receives a recorded session in its place, with the same expiry
   (channel `renewed`): ending the others never signs out the person who asked.
6. **Under RLS like `api_tokens`**: a row is read by its owner (`app.current_user`) or within a scope
   that sees its user, and written by its owner or in platform scope (the sign-in, before any scope).
7. **Rows are kept until they expire** and are not listed after; their removal is a later concern. The
   enterprise edition's validation runs after the row check, unchanged.

## Consequences

- My account lists the person's sessions, says which browser is theirs, and ends one or all the others.
- A sign-out is real on the server; a stolen session cookie can be ended by its owner from any other
  session.
- Every cookie request reads one more row by primary key; a request at most every minute per session
  writes it.
- Downgrading drops the table and the column; cookies with a `sid` keep working (the claim is ignored),
  without a list.
- Not covered: idle expiry (a session holds its eight hours whatever its activity), a cap on the number
  of sessions, an administrator's view of another person's sessions, and the purge of expired rows.
