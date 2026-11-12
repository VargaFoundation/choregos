# 0152 — An agent has watchers, stars and visible forks

- **Status**: accepted, 2026-11-12
- **Concerns**: `apps/api` (`services/agent_social.py`, `routers/agent_social.py`, migration
  `a2c4e6f8b0d1`), `packages/contracts/openapi.yaml`, `apps/web` (agent page, registry, inbox)

## Context

An agent can be forked (ADR 0114) but nothing says who forked it, who follows it, or who finds it
useful. A person learns that a version shipped or that production moved by looking. The registry
cannot rank by use.

## Decision

1. **Forks are listed for the caller**: `GET /orgs/{org}/agents/{slug}/forks` returns only the forks
   the caller may see (the same `visible_clause` as the registry), each with scope, owner,
   `created_at`, `forked_from_version`, its latest version and `diverged`. `Agent.social.forks`
   counts the same set, so two people may read two numbers.
2. **Watch** is `PUT|DELETE .../watch` with a level, `all` or `releases`. The owner watches at `all`
   from creation (and the migration backfills it); a transfer makes the new owner a watcher.
3. **Star** is `PUT|DELETE .../star`, idempotent; `social` carries `stars` and `starred_by_me`.
   `GET /agents` takes `sort=stars` and `starred=true`.
4. **Both are a person's gesture**: the system and an outpost principal get 403. A personal token acts
   as its user.
5. **News goes to an inbox of its own** (`agent_notifications`, `GET .../agent-notifications`, read
   one or all). Written for each watcher on: new version, production moved, candidate started,
   promoted or rolled back (`releases` and `all`); new fork, deprecated, retired (`all` only). The
   actor is never told of their own act.
6. **RLS**: the three tables carry the forced `org_isolation` policy; `agent_notifications` adds
   restrictive owner policies (a row is read, updated, deleted by its user). At read time each item
   is filtered by the reader's current visibility of the agent (and the related agent), and the
   unread count is taken over the visible ones: a watcher who loses access gets nothing, and the
   count is not inflated by what they cannot open.
7. **Audit**: `agent.watched`, `agent.unwatched`, `agent.starred`, `agent.unstarred`.
8. Contract change is additive; the client is regenerated.

## Consequences

- A notification row for an agent the reader cannot see stays in the table, invisible, until the
  access returns or the agent is deleted (cascade).
- Counts are computed per request (one grouped query per list page); no counter column to drift.
- Not done: e-mail or webhook delivery of the news, watching a project or an organisation.
