# 0090 — An organisation's slug is an address, its id is its identity: a rename keeps the old slugs as aliases

- **Status**: proposed, 2026-10-26
- **Concerns**: the contract (`openapi.yaml`: `Org.aliases`, `releaseOrgAlias`), the API
  (`org_slug_aliases`, `services/org_aliases.py`, an ASGI middleware, `routers/projects.py`,
  `routers/auth.py`, `amorcage.py`, `db/session.py`), the console (redirect); builds on
  [ADR 0024](0024-two-editions.md) (editions), [ADR 0026](0026-what-the-core-rls-covers.md) (RLS, identity
  tables) and the memory key of S40-11 (`choregos_core.memory_scope`)

## Context

`PATCH /orgs/{org}` lets a platform administrator change an organisation's slug. The product owner's
requirement is that this "must not break anything: if it does, the slug is used where the organisation's
id, which never changes, should be". The audit of 2026-10-26 (below) confirms the reading: almost
everything already holds the id (`org_id` foreign keys, secrets sealed with `org_id` as additional data,
Temporal ids built from ids, memories keyed by the first slug). What breaks is what holds the slug as an
**address someone else keeps**:

- URLs of the console, MCP gate addresses `/mcp/projects/<org>:<project>`, A2A addresses, qualified ids
  `<org>:<project>` copied into agent configurations and scripts;
- the IdP's groups `choregos:<org>:<group>`, which the IdP's administrator renames on their own schedule,
  and the deployment's `CHOREGOS_OIDC_DEFAULT_ORG` / `CHOREGOS_BOOTSTRAP_ORG`;
- **the slug carried in a long-lived payload**: the knowledge-sync loop (`KnowledgeSync`, which continues
  as new for months), a distillation, a hosted build, the A/B memory report and an OAuth handshake all
  carry the organisation's slug and use it to scope the database session. RLS compares slugs
  (`choregos_org_slug(org_id) = ANY(choregos_current_orgs())`): after a rename those sessions see no row,
  and the loop answers `gone` forever, silently;
- **the bootstrap**: `CHOREGOS_BOOTSTRAP_ORG` creates the organisation when no row has that slug. After a
  rename the next restart creates a second organisation with the old slug (and fails on the community
  edition's single-organisation rule at the first `POST /orgs`).

### Audit (what the slug is, and whether a rename follows)

| Place | Holds | After a rename | Handled by |
| --- | --- | --- | --- |
| `projects`, `memberships`, `api_tokens`, every `org_id` column, `org_secrets` (AAD), `audit_log.org_id` | org id | follows | nothing to do |
| Principal (`org_roles`, `project_roles`), `GET /orgs`, the request's RLS scope | built per request from `memberships` joined to the current slug | follows: a session or API token issued before the rename reads the new slug on its next request | nothing to do |
| Session cookie, API tokens, run tokens (`project` claim) | user id, token hash, project slug (not the org's) | follows | nothing to do |
| RLS policies | the current slug, from `organizations` | follows for requests; **breaks** for a session scoped by a slug read earlier | decision 5 |
| Memories (`memory_key`, namespace `org:<key>`) | first slug, pinned | follows | already done (S40-11) |
| Temporal ids (`wi-`, `train-`, `prov-`, `findings-`: project slug; `hosted-build-`, `agent-session-`, `knowledge-*`: ids) | no org slug | follows | nothing to do |
| `KnowledgeSync`, `KnowledgeDistill`, `AgentBuild`, `MemoryIngestion` inputs | org slug | **breaks** (RLS scope, lookup by slug) | decision 5 |
| Signed OAuth `state` (remote credentials), GitHub App `state` | org slug, valid minutes | breaks if a rename lands in the window | decision 5 |
| `projects.config.org` (required by `project.schema.json`) | org slug | stale, read by no code | rewritten on rename (decision 6) |
| `users.groups` | the IdP's groups, verbatim | follows (not an address) | nothing to do |
| IdP groups `choregos:<org>:<group>`, bare groups for `CHOREGOS_OIDC_DEFAULT_ORG` | slug, in the IdP | **breaks** (no membership is granted) | decision 4 |
| `CHOREGOS_BOOTSTRAP_ORG` | slug, in the deployment's values | **breaks** (a second organisation) | decision 4 |
| Console URLs, REST `/orgs/{org}`, A2A `/a2a/orgs/{org}`, MCP `/mcp/projects/<org>:<p>`, `/projects/<org>:<p>`, CLI configuration | slug, outside the platform | **breaks** | decisions 2 and 3 |
| Runner and pod labels (`choregos/run-id`, `choregos/project`), metrics labels (`project`), network decision rows | run id, project slug, `org_id` | follows (a run in flight keeps a display-only `org` in its log lines) | nothing to do |
| Secret store references | `org_id`, then `project/<slug>/…` | follows | nothing to do |
| Egress allowlist rows, caches | `org_id`; no cache is keyed by an org slug | follows | nothing to do |
| `audit_log` payloads (`avant`/`apres`, `slug`) | the slug at the time | correct: history is not rewritten | nothing to do |

## Decision

1. **The organisation's id is its identity; the slug is a display name and an address.** No new persisted
   key may use the slug. A slug that must be stored by something outside the platform's control (an IdP, a
   URL, a deployment value) is covered by an alias.
2. **A rename keeps the old slug as an alias.** A table `org_slug_aliases(alias PK, org_id, created_at,
   created_by)` holds every slug an organisation has left. The table is identity data and, like
   `organizations`, is outside RLS (ADR 0026). `Org.aliases` lists them (`GET /orgs`, `PATCH /orgs/{org}`).
3. **An alias resolves everywhere a slug is read from outside.**
   - REST and A2A: a middleware rewrites `/api/v1/orgs/<alias>/…` and `/a2a/orgs/<alias>…` to the current
     slug before routing and adds `Link: <current URL>; rel="canonical"` to the response. It is a rewrite,
     not a 308: a `POST` with a body and a WebSocket upgrade survive, and no client has to follow a
     redirect. Routes keep their `{org}` parameter and see the current slug.
   - MCP and qualified ids: `resolve_project` resolves the organisation part (`<org>:<project>`,
     `<org>/<project>`) through aliases and returns the current slug.
   - Console: the organisation list carries `aliases`; a URL whose organisation is an alias is replaced
     (history `replace`) by the current URL.
4. **An alias keeps granting what the slug granted.** IdP group mapping resolves each organisation named in
   a group (after the plugin's mapper, so an edition's mapper benefits too) through aliases;
   `CHOREGOS_OIDC_DEFAULT_ORG` and `CHOREGOS_BOOTSTRAP_ORG` are resolved the same way, so the bootstrap
   finds the renamed organisation instead of creating another.
5. **A slug in a payload or a state is resolved before it scopes a session.** `session_scope(orgs=[…])`
   replaces an alias by the current slug (one query under the platform scope, on PostgreSQL only), and the
   lookups made from a payload (`memory_ab_comparison`, knowledge sync, the OAuth and GitHub App states)
   use `organisation_by_slug_or_alias`. Workflow histories are untouched: the fix lives in the activities,
   so it replays.
6. **A slug is unique across current slugs and aliases.** Creating or renaming to a slug that is another
   organisation's alias is a 409. An organisation may take back one of its own aliases (the alias row is
   removed). The rename also rewrites `projects.config.org`.
7. **An alias is released by the platform administrator**: `DELETE /orgs/{org}/aliases/{alias}`
   (`releaseOrgAlias`, 204). Without it a slug could never be reused. Releasing is the one gesture that
   breaks old addresses, so it is audited (`org.alias.release`).
8. **Rename is audited with its aliases** (`org.update` keeps `avant`/`apres`; the alias is added in the
   same transaction).

## Consequences

- A rename breaks nothing that held the slug, including what the platform cannot reach (an IdP, a script,
  an agent configuration, a deployment's values). The cost is one table and one indexed lookup per
  organisation-scoped request.
- Aliases accumulate with renames; releasing is manual and rare.
- `Org.aliases` is a contract change (ADR 0001): it goes in its own `contract/…` PR; the implementation is
  stacked on it.
- Not covered: a run **in flight** keeps the slug it was started with in its log lines (display only), and
  the IdP's own group names are not rewritten — they keep working through the alias until someone
  renames them.
