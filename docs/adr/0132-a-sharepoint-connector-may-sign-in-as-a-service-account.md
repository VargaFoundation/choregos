# 0132 — A SharePoint connector may sign in as a read-only service account

- **Status**: proposed, 2026-11-09
- **Concerns**: `documents/graph.py`, the `documents/sharepoint` connector type (`username`, the secret
  `password`), its procedure; amends the "Graph with `Sites.Selected` only" point of
  [ADR 0051](0051-knowledge-bases.md)

## Context

ADR 0051 has the Entra application hold the application permission `Sites.Selected`, granted `read` on
the chosen sites, and never `Files.Read.All` nor `Sites.Read.All`. That grant has the size of a site.
Some sites do not fit it:

- a site whose folders are restricted cannot be opened site-wide: an application grant reads past the
  restrictions, because application permissions do not go through the site's memberships;
- granting one folder instead means giving the item its own permissions, which breaks the permission
  inheritance its owners rely on.

What these sites already have is groups: someone who should read the documents is put in a group. A
directory user in those groups reads exactly what the site's owners meant it to.

## Decision

1. A `documents/sharepoint` connector **may name a `username`**, and then holds a `password` among its
   secrets, sealed like `client_secret`. Without `username` nothing changes: the application signs in
   with `client_credentials`.
2. With `username`, the token request is the **OAuth2 password grant** on the same endpoint, with the
   same confidential application and its secret, for `https://graph.microsoft.com/.default`. The token is
   cached the same way. Delta, the folder filter, downloads and egress hosts do not change.
3. The application then holds the **delegated** `Sites.Read.All`, with admin consent. Delegated, it is
   narrowed to what the account may read, which is why it does not contradict ADR 0051's refusal of the
   application permission of the same name: that one opens the tenant.
4. **`username` without `password`, or the reverse, is refused when the source opens.** A declaration
   cannot refuse it: `check_config` sees the configuration without its secrets, and a secret is set,
   replaced or cleared after the declaration, through its own route.
5. **A refusal says little.** The application's stays `token refused (status)`. An account's adds an
   AADSTS code only if it is in a short list an administrator can act on (wrong password, MFA required,
   account locked...). The list is looked up by the code; no text from the endpoint and no part of
   the request is repeated.

## Consequences

- `Sites.Selected` stays the default and the recommendation. The procedure and the console say so.
- **Microsoft discourages the password grant**: the platform holds a user's password, and the flow does
  not work for an account that uses passwordless sign-in or a federated identity provider.
- **The account cannot have an interactive MFA requirement**, so it is a password-only identity. It must
  be dedicated, hold no administrator role, read and nothing more, and be restricted by network location
  (Conditional Access) where possible. Microsoft may also stop issuing such tokens to some APIs as MFA
  enforcement widens; the application sign-in does not depend on this.
- What the source reads is what the account may read: adding the account to a group widens the corpus at
  the next sync, with nothing on the platform's side to approve it. Whoever manages the groups decides.
- The knowledge base's own access rules still apply; a document readable by the account becomes readable
  by everyone the base is open to, as for any source.
- **Not done**: certificate credentials and a declaration-time refusal of a half-configured account.
