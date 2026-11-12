# Plan — 2026-11-12: the terminal is a surface of the platform (stream S67)

Source: the product owner's request of 2026-11-12 (*"the CLI becomes the surface of exchange with the platform
… a clear view of the progress of tasks and of the sub-agents that are launched … stylish, with colour"*) and
their approval, the same day, of the workspace's mock-up and of one rule for every developer surface (the
terminal, and the editor extension of stream S68). Decision: [ADR 0154](../adr/0154-the-terminal-is-a-surface-of-the-platform.md)
(proposed). Checked against `main` at `36a8cadee`. Stream number: **S67** (S66 is taken by two streams in flight,
ADR 0152 and ADR 0153).

Status words: **exists** (on `main`), **missing**, **branch** (an open pull request).

## 0. What the audit found

| What a developer surface needs | Where | Today |
|:--|:--|:--|
| List my assistants, open, list, resume a conversation | `/me/assistants`, `/me/assistants/{org}/{slug}/conversations`, `/me/conversations[/{id}]` | **exists** (S63-04, S63-18) |
| A streamed turn, cancelled when its client leaves | `POST /me/conversations/{id}/messages` `stream: true` | **exists** (S63-04b) |
| Citations, a confirmation link, accounts to connect, in the REST answer | `services/conversation_signals.py` | **missing** in REST; the MCP door's answer has them (S63-11) |
| Follow a turn started elsewhere, a turn that outlives its client | — | **missing** |
| The proposed writes of a conversation | `GET /me/conversations/{id}/actions` | **exists** (S63-10) |
| What is in flight, as a list and a stream | `/orgs/{org}/activity[/stream]` | **exists** (S64-05); turns **missing** until S64-09c |
| A run's journal, context, stop | `/runs/{id}/events` (SSE), `/runs/{id}/context-breakdown`, `/runs/{id}/cancel` | **exists** (S64-03, S64-08, S64-08b) |
| Sub-agents of a run | the ACP `tool_call` updates in the journal, verbatim | **missing** as nodes: no rule says which call delegates |
| What waits for me, across projects and conversations | MCP `list_pending_decisions` (per project), `/orgs/{org}/actions` (governed actions only) | **missing** |
| A sign-in of the CLI with an authentication time | `choregos login --token` stores a pasted `*` API token | **missing** |
| Decisions refused to an API token | `services/actions.decide` (governed actions) | **exists** for governed actions; **missing** for `postDecision` and `confirmMyConversationAction`, which a `*` token passes |
| Launches proposed by an assistant | ADR 0153 | **branch** (#1647-#1650) |
| A TUI library in the binary | Rich only | **missing** (Textual) |

## 1. Stories

Order of the pull requests: S67-00, S67-01, S67-02 (stacked, routes that exist), then S67-03 and S67-09 (the
workspace, reading and conversing); S67-04a then S67-05 and S67-06 (sign-in, then the rule); S67-04b then S67-07
and S67-08; S67-10 once S67-05, S67-06, S67-07 are merged and ADR 0153 is accepted; S67-11, S67-12, S67-13 last.

### S67-00 · docs(adr): ADR 0154, this plan, the backlog stories

**Accepted when** the index lists ADR 0154 with its status, ADR 0139 carries the note of its amendment, and the
stories are in the backlog and in STATUS.

### S67-01 · feat(cli): `choregos ask`, one turn of a durable conversation, streamed and scriptable

`choregos ask [MESSAGE|-] [--assistant SLUG] [--conversation ID | --continue] [--json] [--org]`. The assistant is
the one given, or the only ready one of the person's audience in the organisation (otherwise the command names
them); the answer is streamed to standard output and nothing else is written there; the conversation id, the
turn's cost and every write the assistant proposed during the turn, with its confirmation link, go to standard
error. **Accepted when** the deltas are printed in the order they came and nothing but them is on standard output,
a resumed conversation sends no `open`, `--continue` picks the assistant's most recent conversation, a `failed`
turn and an `error` event exit 1 with the reason, `--json` prints one object validated by a test, the proposed
writes printed are the ones of this turn only, `NO_COLOR` output has no escape sequence; each red with its guard
reverted. Fakes: the API mocked with respx, payloads validated against `openapi.yaml`.

### S67-02 · feat(cli): `choregos activity`, the live tree of what runs, with colour

`choregos activity [--watch] [--project] [--workflow] [--agent] [--kind]`: the organisation's activity as a
tree project → work item → run (stage · agent · model) and the person's sessions, each line with a status glyph
and word, elapsed time, tokens and cost so far ("—" when unknown), then what ended in the last hour; a status line
`N running · $X today`; `--watch` applies `activity.upsert` and `activity.ended` from the stream until interrupted.
**Accepted when** each status renders its glyph and word, unknown spend renders "—" and never 0, the filters are
sent as query parameters, an `activity.ended` moves an entry from in flight to finished, a 403 on the costs leaves
"$ —", `NO_COLOR` gives no escape sequence and an ASCII encoding gives ASCII glyphs; each red with its guard
reverted.

### S67-03 · feat(cli): a run's steps under its node — tool calls, plan, context

`choregos activity --run ID` (and the workspace's expanded node): the run's ACP journal (`/runs/{id}/events`,
`after_seq`) reduced to its plan and tool calls (title, status, elapsed), the context breakdown's latest sections.
**Accepted when** a recorded journal of a claude-code run and one of an opencode run reduce to the expected steps,
an unknown update kind is skipped and counted, and a run with no breakdown says "not measured".

### S67-04a · contract(openapi): a developer-surface sign-in (ADR 0154 §6)

`POST /auth/device`, `POST /auth/device/token` (RFC 8628 errors `authorization_pending`, `slow_down`,
`expired_token`, `access_denied`), the console's `/device` approval route, `DELETE /me/sessions/{id}` covering
surface sessions, `Session.kind` (`browser`, `cli`, `editor`). **Accepted when** `make contracts` is regenerated
and the operations are listed contract-first.

### S67-04b · contract(openapi): what the developer surfaces read (ADR 0154 §4, G1, G2, G5, G6, G8)

`AgentResponse.citations`, `.pending_actions[{id, summary, confirm_url}]`, `.missing_accounts`; `GET
/me/conversations/{id}/events` (SSE, `Last-Event-ID`) and `ConversationMessageCreate.detach`; `GET /me/pending`
(`PendingItem`: kind `human_step|governed_action|conversation_action|launch`, what, where, since, the decision
route); `ActivityEntry.work_item_title`; `GET /runs/{id}/agents` (`RunAgentNode`: id, parent, kind `agent|subagent`,
tool, status, started, ended, tool calls). **Accepted when** `make contracts` is regenerated and the guards list
them contract-first.

### S67-05 · feat(api,web,cli): `choregos login` signs in as the person (ADR 0154 §6)

The device flow brokered by the console, a surface session (`browser_sessions` with a kind, an idle expiry, the
sign-in's `auth_time`), `choregos login` (device by default, `--token` kept), `choregos login --fresh`, `choregos
logout`, the sessions on My account. **Accepted when** a device code is single use and expires, polling faster
than `interval` answers `slow_down`, an unapproved code answers `authorization_pending`, an approved one yields a
session whose principal has `authenticated_at` equal to the approving sign-in's, revoking it on My account makes
the next CLI call 401, a session of another organisation's provider is refused, and the CLI never prints nor
takes the credential as an argument.

### S67-06 · feat(api): a decision or a confirmation needs the person's session (ADR 0154 §7, G10)

`postDecision`, `decideProjectAction` (already), `confirmMyConversationAction`, `refuseMyConversationAction`, the
release approval and, once merged, the launch confirmation of ADR 0153 refuse a principal without a session
(`decision_requires_session`); a deployment setting keeps the old behaviour of `postDecision` and the
conversation actions for one minor version; the upgrade notes say so. **Accepted when** each route refuses a `*`
API token and a machine account and accepts a surface session and a console cookie, the setting reopens exactly
the two routes it names, and the notice is in `docs/deployment.md`.

### S67-07 · feat(api): what waits for me (G8)

`GET /me/pending`: the human steps addressed to the person or their groups that they may decide, the governed
actions they may approve (rank, group, separation of duties applied), the proposed writes and launches of their
conversations; newest first, each with its decision route and console address. **Accepted when** a person sees no
step of a project they cannot read, a step addressed to a group they are outside of, nor an action they proposed
themselves (separation of duties), and each kind is listed from a fake of its own.

### S67-08 · feat(api): the REST turn says what the door says, and a turn can be followed from elsewhere (G1, G2)

`citations`, `pending_actions` with `confirm_url` and `missing_accounts` on the REST turn (the door's
`conversation_signals`); `GET /me/conversations/{id}/events` replays a turn in flight from its journal and follows
it; `detach: true` keeps the turn running when its client leaves. **Accepted when** the REST answer and the door's
are equal for the same turn, a second client attached mid-turn receives the rest of the deltas, `Last-Event-ID`
resumes without repeating a delta, and a detached turn completes and is booked once after its client closed.

### S67-09 · feat(cli): `choregos chat`, the workspace in the terminal (Textual)

Conversation (left), live tree (right, blue), decisions (amber, read-only until S67-10), elsewhere (below),
status line; keys to send, cancel, switch conversation, expand a node, stop with a reason. A bare `choregos` opens
it when stdin and stdout are terminals. Textual imported by `chat` alone; `--include-package-data=textual` in
`tools/cli_binary/build.py`; the smoke test starts it headless. **Accepted when** Textual pilot tests drive a turn
and a stop against the faked API, SVG snapshots of the workspace (colour and `NO_COLOR`, 80 and 160 columns) are
committed, `choregos --version` imports no Textual module (a test), a bare `choregos` piped prints the help, and
the binary's size stays within ADR 0151's budget.

### S67-10 · feat(cli): decisions and confirmations from the terminal (ADR 0154 §7, ADR 0153)

The decisions pane and `choregos decide`: confirm or refuse a proposed write or launch, decide a human step,
approve or reject a governed action, each after showing its operation, arguments and diff, one explicit gesture
per action; `step_up_required` leads to `choregos login --fresh`. **Accepted when** a confirmation sent with a
pasted token is refused with the API's message and the CLI says how to sign in, a confirmation with a surface
session reaches the route once, a rejection asks a reason, and no batch confirmation exists.

### S67-11 · feat(cli): artefacts — list, download, open, compare

A work item's artefacts and the person's inbox in the elsewhere pane and `choregos artifacts`: download to a
path (never overwriting without `--force`), open with the system's viewer, compare two versions, a run's diff.
**Accepted when** a download writes the bytes the API served, an existing file is kept without `--force`, and a
diff of two versions of a text artefact is shown.

### S67-12 · feat(runner,api,cli): sub-agents are nodes of the tree (ADR 0154 §5, G5)

A per-backend rule beside each ACP backend's adapter names a delegating tool call and its parent; `GET
/runs/{id}/agents` serves the nodes; the tree shows them under their run. **Accepted when** recorded transcripts
of claude-code and opencode delegating to a sub-agent give the expected nodes, a backend without a parent id gives
a flat list and no sub-agent, and a surface never reads `_meta`.

### S67-13 · docs: the terminal in the user documentation

`docs/site/use/terminal.md`: install, sign in, ask, follow, decide, colours and `NO_COLOR`; `docs/cli.md`
regenerated. **Accepted when** `tests/docs` is green and every command of the page runs in the documentation's
own test.

## 2. What this plan does not do

- No local tool, model, agent or file edit (ADR 0154 §1); no offline mode; no administration screens.
- Tokens per sub-agent (the gateway counts per run key).
- Conversation turns in the activity list are S64-09c's, not this stream's.
- The editor extension is stream S68 (ADR 0155), which reuses S67-04a, S67-04b and S67-05 to S67-08.
