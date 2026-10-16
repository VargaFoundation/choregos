# 0075 — An agent reads several memories, global and project, and writes to one

- **Status**: accepted, 2026-10-16
- **Concerns**: `memory_stores`, `memory_facts`, `AgentSpec.memories`, `StageInput.memories`,
  `MemoryItem.memory`, the memory routes, `prepare_stage`, ingestion; keeps
  [ADR 0007](0007-optional-memory.md) (memory is optional, never blocks a stage) and
  [ADR 0026](0026-what-the-core-rls-covers.md) (every organisation table under forced RLS); amends
  the S32-02 behaviour (`memory: none | project | agent`); keeps knowledge bases apart
  ([ADR 0051](0051-knowledge-bases.md) §1)

## Context

An agent used exactly one memory per run, chosen by a three-value enum (`AgentSpec.memory`): the
project's shared memory, the agent's own memory within the project, or none. There was no memory
of the organisation at all: memory is a per-project connector, every route is
`/projects/{id}/memory/*`, the lexical store keys every fact on a project and Ecphoria is addressed
with the project's slug. What an organisation learns once (security lessons, architecture decisions)
cannot be shared across its projects, and a project cannot keep several memories apart.

Vincent asked for an agent that uses one or several global memories **and** one or several project
memories at the same time.

## Decision

1. **Named memories.** A memory is an organisation object, `memory_stores`:
   - **global** (`scope: global`): the organisation's, curated by an `org_admin` (`agent:manage`, as
     knowledge bases). Its **reach** says whose runs may read it: `org` (every project) or `projects`
     (the listed project slugs).
   - **project** (`scope: project`): a project's named memory, created and archived by its owners
     (`project:write`, as project-owned knowledge), read by the runs of that project only.
   - Two **implicit** project memories exist in every project and are never rows: `shared` (the
     project's memory of today, namespace `<project>`) and `own` (the agent's own memory in that
     project, `<project>#agent:<slug>`). `shared` and `own` are reserved slugs.
2. **The binding is a list on the version**: `AgentSpec.memories: [{scope: global|project, slug,
   mode: read|write}]`, at most 8, no duplicates, **at most one `write`** (write implies read).
   A `project` reference is **relative to the run's project**: an organisation agent runs in many
   projects, and a run never reads another project's memory. **Order is precedence**, earlier wins.
   The **write target** is the one `write` binding: the run's lessons and findings and the agent's
   proposals go there. A **global** write target only ever receives **proposals** (pending, decided
   by an administrator); the auto-accept rule applies to project memories only. No `write` binding:
   the run writes nothing.
3. **No version is rewritten.** `memories` is optional and **absent means "derive from `memory`"**:
   `project` is `[{project, shared, write}]`, `agent` is `[{project, own, write}]`, `none` is `[]`.
   The digest (`empreinte`) leaves `memories` out while it is unset, so no existing checksum moves.
   Sending `memories` with a non-default `memory` is a 422. `AgentVersion.effective_memories` is the
   derived list, so readers never re-implement the mapping.
4. **Resolution per run** happens in `prepare_stage` (an activity: no workflow change). A global
   memory must exist, be active and reach the run's project; a named project memory must exist and be
   active in the run's project; `shared` and `own` always resolve. An unresolvable binding is
   **skipped, never fatal** (ADR 0007) and journaled. The resolved list travels in
   `StageInput.memories`; `memory_scope` stays, set to the legacy equivalent when there is one, so
   runs started before the change and old runners keep working.
5. **Recall.** Each read memory is queried concurrently; the packs are merged by
   `merge_context_packs`: items are **deduplicated by subject, the highest precedence winning**, then
   taken **round-robin by precedence** until the token budget is spent. Round-robin rather than a score
   sort because scores of different stores (Ecphoria, lexical) are not comparable and one large memory
   would crowd the others out. Each item carries its memory's label (`MemoryItem.memory`).
6. **Storage.** Namespaces (`choregos_core.memory_scope`): `<project>` (shared),
   `<project>#agent:<slug>` (own), `<project>#memory:<slug>` (named project), `org:<org>#memory:<slug>`
   (global). `parse_namespace` reads all four; `split_namespace` keeps the two old forms and **raises**
   on the new ones, so no adapter can silently read a named memory as the shared one.
   `memory_facts` gains `org_id` (backfilled from the project, then NOT NULL) and `memory_store_id`;
   `project_id` becomes nullable under a CHECK that a fact has a home; the RLS policy keys on
   `org_id`. Ecphoria: tenant `org:<org>` for a global memory, and the full namespace in
   `X-Ecphoria-Namespace` for every namespace but the shared one. A global memory is served by the
   organisation's `memory` connector when it declares one, else by the deployment default.
7. **Permissions.** Global memories: create, edit, change reach, archive, decide proposals are
   `agent:manage`; seeing and searching follow the reach (administrators see all; what a caller may not
   see is 404). Project memories: create and archive are `project:write`; search is for project
   members; deciding proposals is `memory:write` as today. Publishing a version that binds a global
   memory requires that the publisher **sees** it (422 "unknown memory", never revealing one that
   exists); a project agent (ADR 0061) binding a named project memory is checked against its project.
8. **Review gap closed on the way.** `/projects/{id}/memory/search|pending` accept `?memory=<slug>`
   (default `shared`; `own` with `&agent=<slug>`), so an agent's own memory and named project memories
   can be reviewed. A decision is scoped to the namespace it is made in.

## Alternatives rejected

- *A per-binding token budget*: more knobs; precedence plus round-robin give a fair default, and a
  `share` field can be added later without breaking the shape.
- *Several write targets*: a fact written twice diverges, and which copy wins on supersession?
- *Absolute references to another project's memory*: a run would read a project it does not belong
  to; promote that memory to global, with a reach.
- *An agent-wide "own" memory across projects*: it leaks what an agent learnt in project A into
  project B; a global memory with a reach does it explicitly.
- *Rewriting stored versions to the new field*: breaks immutability and every checksum.
- *Making memory failures fatal for an explicitly bound global memory*: contradicts ADR 0007.
- *Reusing knowledge bases for global memory*: ADR 0051 keeps them apart (a read-only corpus against
  facts learned and proposed).

## Consequences

- Stage S40-11a (this record's first PR) ships the contract, the API and the storage; running the
  bindings is S40-11b, the console S40-11c.
- Ecphoria's acceptance of the `org:<org>` tenant and of the namespace header is untested against a
  real Ecphoria (as for S32-02).
