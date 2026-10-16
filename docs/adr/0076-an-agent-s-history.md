# 0076 — An agent's history: versions with a message, restored as new

- **Status**: accepted, 2026-10-16
- **Concerns**: `agent_versions.message` and `restored_from` (migration `e6b8d0f2a4c5`), the endpoints
  `publishAgentVersionWithMessage` (`POST …/versions/publish`) and `restoreAgentVersion`
  (`POST …/versions/{n}/restore`), `AgentVersion.pinned_by`, `AgentCreate.message`, `AgentFork.message`,
  the problem members `code` / `latest_version` / `latest_author`, and the console's Definition and
  History tabs (S40-12b); amends [ADR 0033](0033-agent-registry-and-skill-library.md) §1 ("a change
  publishes the next version" becomes "from a base, with a message")

## Context

An agent's versions are immutable (ADR 0033 §1), but nothing says *why* a version exists, nothing
protects two people editing from the same version (the second silently buries the first's intent),
an unchanged spec still makes a new version with the same checksum, and going back to v2 means
retyping it. The product owner's request 12 (S40) was: the Definition page is not clear, between the
versions and the options one gets lost; there must be a history and revision system that makes it
readable.

## Decision

1. **A revision is an `agent_versions` row.** The console says *revision* (`revision v3`, the History
   tab, "Restore v1 as a new revision"); the API, the DSL (`agent@3`) and project pins keep the word
   *version*: renaming the API would break every client for a word. This follows the vocabulary of
   the lifecycle story (S40-02): *publish* saves the next revision, *release* points a channel at one.
2. **A version carries a message** (`agent_versions.message`, 1 to 500 characters, required by the
   new publish endpoint) and, for a restore, `restored_from` (the version copied). Author and date
   already exist. A version published before has no message and says so (nothing is backfilled).
3. **Publish from a base.** `POST /orgs/{org}/agents/{slug}/versions/publish {spec, message,
   base_version}`: 409 `stale_base` when the latest is not `base_version` (the problem carries
   `latest_version` and `latest_author`), 409 `no_change` when the spec's checksum equals the
   latest's, 409 `eval_failed` when a case of the suite fails (unchanged gate), otherwise a new
   version. The original `POST …/versions` (a bare spec) stays, with no message, documented as
   superseded: no client breaks, and it still accepts a repeated spec.
4. **Restore is a new version.** `POST …/versions/{n}/restore {message?, base_version}` copies the **stored**
   spec and checksum of version N (not a re-serialisation, so a version written before a field
   existed keeps its bytes) as the next version, with `restored_from = N` and the default message
   `Restore vN`. Same rights, same 409s and same eval gate as a publication; restoring the current
   spec is `no_change`. Nothing is rewritten or deleted. The console never calls this *rollback*
   (S40-02 reserves that word for moving production back) nor *promote* (ADR 0061).
5. **The platform writes the messages it can**: `Created` (or `AgentCreate.message`), `Installed from
   the catalogue (architect@2)`, `Catalogue update to architect@3`, `Forked from developer@2` (or
   `AgentFork.message`).
6. **Who uses a version**: `AgentVersion.pinned_by` lists the slugs of the projects pinning it that
   the caller may see (an organisation admin: all; anyone else: the projects they hold a role in).
   When the lifecycle story's exposure endpoint lands, which also covers channel followers, the
   console reads that instead.
7. **The diff is computed in the console**, field by field, from two specs it already has: no API
   endpoint (the CLI and the MCP door can diff two `getAgentVersion` answers).
8. **The console** shows one version, read-only and grouped, on the Definition tab (with what runs by
   default and who pins what); *Edit* is a deliberate mode with a live "Changes" panel, a required
   "what changed and why" and the stale-base answer; *History* lists revisions with messages, compares
   any two and restores. No draft is stored on the server: the editor is the draft, `base_version`
   catches the concurrent publish.

## Interface with the lifecycle story (S40-02)

A revision is immutable and numbered once; channels point at revision numbers; publish and restore only
append and never move a channel. The console reads "which revision is current" in one function
(`currentVersion`), and renders channel badges on History from the version's `channels` once they exist.
The `lifecycle` tab belongs to that story; this one owns `definition` and `history` (`?tab=versions`
still opens History).

## Consequences

- A concurrent publish is now a 409 with a way forward (compare, publish on top), not a silent overwrite.
  The base check and the insert share a transaction but not a lock: two publishes in the same instant
  race on the unique `(agent_id, version)` constraint, which turns into an error for the loser.
- The new endpoints add two routes and three optional fields; every existing client keeps working.
- Audit: `agent.publish` carries `restored_from` and `message_chars` (never the message text).

## Alternatives discarded

- **Server-side drafts** (a working copy per agent): a second state to explain, locks, and a conflict
  with the trial, which already runs an unsaved spec.
- **A diff endpoint**: a pure function of two documents the client has.
- **Changing the body of `publishAgentVersion` to `{spec, message}`**: breaks every client; a new
  operation is additive.
- **A line diff of the JSON**: unreadable for lists (a skill moved shows as two changes).
