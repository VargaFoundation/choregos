# 0081 — Knowledge base rights: reach serves runs, grants give people roles

- **Status**: accepted, 2026-10-21 (defaults D11-1 to D11-5 below are to be confirmed by the owner)
- **Concerns**: `knowledge/access.py` (`decide`), `routers/knowledge.py`, `services/agent_drafts.py`,
  the routes `GET`/`PUT /orgs/{org}/knowledge/{slug}/grants`, the table `knowledge_grants`, the column
  `knowledge_bases.restricted`; amends [ADR 0051](0051-knowledge-bases.md) (section 2, who curates; section 12,
  ownership by a project); builds on [ADR 0061](0061-agent-ownership-scopes-and-forks.md) and
  [ADR 0074](0074-catalogue-rights.md) (the server decides `can`)

## Context

- A base had two states: curated by an organisation administrator, or by the owners of one project.
  Nobody else could be given the right to feed or share it, and nothing could hide it from people.
- The *reach* (`scope`, `projects`, `groups`) answers "which runs are served this base". It was also used,
  by default, as "who may read it". Both questions deserve separate answers: a confidential base can be
  served to a project's runs without every project member browsing it.
- The Confluence connector reads with one service account and mirrors what it sees: the platform, not the
  source, is then the confidentiality boundary.

## Decision

1. **Two questions, two mechanisms.** The **reach** serves runs (unchanged; `served_to_project` ignores
   grants and `restricted`). **Grants** give people roles on one base: `viewer`, `editor`, `owner`.
2. **`restricted`** (new, default false). A restricted base is read by people only through a grant (or as
   an administrator or an owning project's owner); its runs are still served.
3. **One server function** decides: `decide(principal, org, base, action, grants)`, actions `read`, `edit`,
   `distill`, `share`, `archive`, `widen`. DTOs carry `can`, `cannot_reason`, `my_role`; the console does
   not guess a right from a role.

| Gesture | Org admin | Owner project's owner | `owner` grant | `editor` grant | `viewer` grant / reach |
|---|---|---|---|---|---|
| read | yes | yes | yes | yes | yes |
| upload, source, sync, rename, serving mode | yes | yes | yes | yes | no |
| distil | yes | yes | yes | yes (D11-1) | no |
| share (edit grants) | yes | yes | yes | no | no |
| archive | yes | yes | yes | no | no |
| widen (scope, projects, groups, `restricted`) | yes | no | no | no | no |

4. **Grants** name a user (by member id, or by e-mail: D11-4), a group (IdP group of the person) or a
   project (its explicit members; an organisation-wide role is not membership). An e-mail naming a member is
   stored as that member's id; otherwise it is kept lower-cased and reads `pending` until the person is a
   member of the organisation. A grant never crosses organisations: only members count.
5. **Widening stays the administrator's** (D11-2): a project owner never decides what other projects read.
   Widening scope still clears `owner_project`; changing `restricted` alone does not.
6. `PUT .../grants` replaces the list (at most 200), is idempotent, and journals `knowledge.grant.set` with
   added, removed and changed subjects only when something changed. `GET` is answered to callers who can
   share; other readers get `403`.
7. The whole base is synced by anyone who may edit it (the route required `agent:manage` while every source
   route accepted the owners).
8. No draft state (D11-5): a base is live as soon as it exists.

## Consequences

- A base the caller cannot read is `404`, not `403`; lists omit it; the agent drafter names only bases the
  caller reads.
- Listing loads every grant in one query, never one per base.
- Not decided here: rights on memories ([ADR 0075](0075-an-agent-reads-several-memories.md)) are a later
  candidate for the same function.
- The migration adds `knowledge_bases.restricted` and `knowledge_grants` under forced row-level security.
