# 0134 — Every person has a personal space; conversations are durable

- **Status**: proposed, 2026-11-10
- **Concerns**: `projects` (`kind`, `owner_user_id`), `agent_sessions` and `agent_session_events`
  (`services/agent_sessions.py`, `routers/agent_sessions.py`), the gateway key of a session, the cost
  ledger, the routes `/me/conversations/*` and `/me/assistants/{org}/{slug}/conversations`; amends
  [ADR 0080](0080-hosted-prompt-and-voice-agents.md) §10 (sessions); builds on
  [ADR 0026](0026-what-the-core-rls-covers.md), [ADR 0047](0047-a-budget-is-a-reservation.md) and
  [ADR 0133](0133-a-plugin-ships-an-assistant-an-organisation-installs-it-for-an-audience.md)

## Context

A session (ADR 0080 §10, S41-09) belongs to a project the caller chooses and holds `item:control` in. It has
one gateway key minted for one hour at the smallest of the caps a run would meet (`_reserve`), then closes;
it cannot be resumed, listed or titled; its answer is computed whole and sent as one SSE delta; its
messages are kept with no retention, and the purge of runs does not reach it.

An assistant's user is not a member of any project, talks to it across days, and owns what the
conversation leaves behind: the transcript, the memory, the accounts they connected, the money they spent.

## Decision

1. **A personal space is a project of kind `personal`.** `projects.kind` is `standard` (every existing row)
   or `personal`; a personal project has `owner_user_id` and one exists per person and organisation (a
   unique partial index), created at the person's first conversation, never by hand. It reuses everything a
   project carries — the cost ledger, budgets, work items for tasks (ADR 0137), memory — and is left out of
   every project listing (`GET /orgs/{org}/projects`, the MCP door's `list_projects`, the console). Only its
   owner reads it: a row-level policy keyed on the request's user (`app.current_user`) is added on top of the
   organisation's, so a bug in a router cannot show one person's space to another. Its slug is
   `personal-<12 hex of the user id>` and reserved; no membership row is written, the owner's rights in it
   are derived.
2. **A conversation is an extended session.** The table keeps its name (`agent_sessions`, with its events)
   and gains `installation_id`, `title`, `context`, `resolved_subjects`, `last_activity_at`,
   `retention_until` and `agent_revision`. The word in the API and the console is *conversation*. A
   conversation of an assistant lives in its caller's personal space; `POST
   /orgs/{org}/agents/{slug}/responses` keeps opening a session in the project given, as today.
3. **A key per turn, not per session.** Each turn mints a gateway key (`turn:<conversation>:<n>`) reserved
   at the smallest of: what is left of the conversation's cap (`per_conversation_usd` minus what it spent),
   of the person's month on this installation (`per_user_month_usd`), of the installation's month, and the
   caps ADR 0047 already applies. The key is revoked when the turn ends and its spend booked with the
   conversation. A turn the cap refuses is refused before the model is called.
4. **A conversation closes on inactivity, and reopens on demand.** `idle_close_minutes` (240 by default)
   after its last activity a conversation is `idle`; a new message reopens it with its history. The
   one-hour expiry of sessions (`SESSION_TTL_S`) goes, for assistants and for `/responses` alike, because
   the key no longer outlives a turn. A conversation keeps the agent revision it started with, as a work
   item keeps its workflow version.
5. **Listed, titled, resumable.** `GET /me/conversations` (title, assistant, last activity, status), `GET`
   and `DELETE /me/conversations/{id}`, `POST /me/conversations/{id}/messages`. The title is generated from
   the first exchange by the same model, within the turn's key. Only the owner reads a conversation; the
   RLS of point 1 holds it.
6. **Streaming is real.** A turn is relayed as the gateway streams it (`response.output_text.delta` per
   chunk), tool calls are journaled as they happen, and a turn may be cancelled; the tool calls already made
   stay in the journal.
7. **A conversation is bounded.** `max_turns` per conversation from the assistant's policy; past a token
   threshold the oldest turns are summarised and the summary kept as a system message.
8. **Retention is code.** `retention_until` is set from the installation's policy at each turn; the daily
   purge (ADR 0133 point 6, S63-16) deletes expired conversations with their events. Sessions finally
   enter a purge.

## Migration

Open sessions are closed (`reason: migrated`) by the migration that adds the columns; their spend is booked
as a close would. Existing tests of `/responses` keep passing unchanged: a session opened there is a
conversation with no installation, in the project given.

## Alternatives rejected

- **A new `conversations` table and a compatibility view over `agent_sessions`**, as the specification
  proposed. Two names for one object in the ledger's foreign key, the events and the hosted session pods;
  the rename buys nothing the columns do not.
- **Keeping one key per session and lengthening it.** A key that lives for days is a credential that lives
  for days, and its reservation holds money the person may never spend.

## Consequences

- `projects` gains two columns and a second RLS policy; `test_rls_postgres.py` proves another person of the
  same organisation reads nothing of a personal space.
- Budgets by person and installation are new caps of the reservation (S63-15).
