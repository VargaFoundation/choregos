# 0154 — The terminal is a surface of the platform

- **Status**: proposed, 2026-11-12 (the product owner's request of the same day; the screen model and the
  colours of its mock-up approved the same day)
- **Concerns**: `packages/cli` (new commands `ask`, `activity`, `chat`, a bare `choregos` in a terminal, a
  `theme` module, `login`/`logout`), `tools/cli_binary/` (Textual in the binary), the routes the developer
  surfaces read (listed in §4), a developer-surface sign-in (§6), the decision and confirmation routes (§7);
  amends [ADR 0139](0139-an-assistant-through-the-mcp-door-and-an-embedded-component.md) §5; extends
  [ADR 0030](0030-an-mcp-door-for-external-clients.md) §5 and [ADR 0035](0035-governed-actions-in-the-core.md)
  to developer surfaces; builds on [ADR 0120](0120-colour-orients.md),
  [ADR 0134](0134-every-person-has-a-personal-space-conversations-are-durable.md),
  [ADR 0141](0141-tokens-and-the-context-are-read-per-scope.md),
  [ADR 0142](0142-a-stop-interrupts-what-is-in-flight.md),
  [ADR 0151](0151-the-cli-ships-as-native-binaries.md) and, once accepted, ADR 0153 (an assistant proposes to
  start a project's workflow); a code editor extension (ADR 0155, stream S68) reuses §2, §4, §6 and §7;
  stream S67

## Context

The product owner (2026-11-12): *"The CLI becomes the surface of exchange with the platform. Instead of using a
coding agent directly to design and develop, everything happens remotely on the platform, with a clear view of
the progress of tasks and of the sub-agents that are launched — something stylish, with colour."*

The `choregos` CLI (Typer, Rich, httpx; native binaries since ADR 0151) files work items, decides human steps,
follows one run's journal, pushes workflows and knowledge, manages plugins. It cannot converse, it shows no live
view of what runs, and it cannot confirm what an assistant proposed. Its sign-in stores an API token pasted from
the console (`choregos login --token`), a credential with no authentication time.

Meanwhile the platform already holds what such a surface needs to read: durable conversations a person resumes
anywhere (ADR 0134), a streamed turn, what is in flight as one list and a stream (ADR 0142 §1), each run's ACP
journal, its tokens and its context composition (ADR 0141), stops with a reason (ADR 0142 §2-4). The console and
the MCP door are two surfaces of the same assistants (ADR 0139 §1). A terminal, and soon an editor, are two more.

Coding agents that run on the person's machine (Claude Code, opencode) exist; Choregos runs them as runtimes in
its sandbox, governed. A terminal surface that ran tools locally would be one more of them, outside the
governance this platform exists for.

## Decision

1. **The terminal is a surface, not an agent.** `choregos` talks to the platform's assistants and shows the
   platform's work; it never runs a tool, a model or an agent on the person's machine, never edits the person's
   files on an agent's behalf, and holds no model key. Everything an agent does happens in the platform's
   sandbox, under its budgets, audit and approvals. The same holds for every **developer surface** (the
   terminal, and the code editor extension of ADR 0155): a developer surface is a client of the REST API under
   the person's identity, with no route of its own (ADR 0139 §1 extended).

2. **One screen model, surface-neutral.** A developer surface's workspace has five parts, which the terminal
   draws with Textual and the editor with its own views, from the same routes (§4):
   - **Conversation** (left): the conversation with an assistant, streamed, with its citations and the writes it
     proposes. A conversation is the durable one of ADR 0134: the terminal opens, lists and resumes the same
     conversations as the console and the MCP door, and a conversation started in one is continued in another.
   - **Live tree** (right, blue): organisation → project → work item → run (stage) → agent session →
     sub-agents and tool calls, each node with its status (a glyph and a word), elapsed time, tokens and cost so
     far (ADR 0141 §2: unknown is "—", never 0), and the stop it allows (ADR 0142, a reason always asked).
   - **Decisions** (amber): what waits for the person — a human step addressed to them or their group, a
     governed action they may approve, a write or a launch an assistant proposed in their conversations — each
     with what it does, its diff when the source gave one, and the gesture that decides it (§7).
   - **Elsewhere** (below): what moved outside the focus — a conversation active on another surface, a work
     item a conversation started (ADR 0153 `launch_status`), an artefact delivered (`/me/inbox`, a work item's
     artefacts, a run's diff), to open, download or compare.
   - **Status line**: `N running · N waiting for you · $X today`, each figure a read of §4, "—" when the person
     may not read it.

3. **Two commands, then the workspace.**
   - `choregos ask [MESSAGE]` is scriptable: one turn (or `-` for standard input), the answer streamed to
     standard output and nothing else there; the conversation's id, its cost and the proposed writes with their
     confirmation link on standard error; `--json` prints one object (`conversation_id`, `status`, `text`,
     `pending_actions[{id, summary, confirm_url}]`, `usage`); `--conversation ID` and `--continue` resume;
     exit code 0 for a completed turn, 1 otherwise. Interrupting it closes the stream, which cancels the turn
     server-side (S63-04b); its spend is booked once.
   - `choregos activity` prints the live tree once, `--watch` keeps it live from the activity stream until
     interrupted, with the filters of `GET /orgs/{org}/activity` (`--project`, `--workflow`, `--agent`,
     `--kind`).
   - `choregos chat` opens the workspace (§2) in the terminal; a bare `choregos` does the same when both its
     standard input and output are a terminal, and prints the help otherwise, so no script changes behaviour.

4. **Live data comes from the platform's existing routes; what is missing is a contract change.** Per part:

   | Part | Routes that exist on `main` | Gaps (stories of S67, one list for every developer surface) |
   |:--|:--|:--|
   | Conversation | `GET /me/assistants`; `POST /me/assistants/{org}/{slug}/conversations`; `GET /me/conversations`, `GET /me/conversations/{id}`; `POST /me/conversations/{id}/messages` (`stream: true`: `response.created`, `response.output_text.delta`, `response.completed|failed`, `error`); `POST /me/conversations/{id}/cancel`; `GET /me/conversations/{id}/actions` | **G1** the REST turn does not carry what the door's answer carries (ADR 0139 §3): `citations`, `pending_actions[].confirm_url`, `missing_accounts` (computed by `services/conversation_signals.py` for the door only; the CLI cannot know the console's address). **G2** a turn lives only as long as the request that streams it: no route attaches to a turn in flight (a turn started in the console cannot be followed from the terminal) and no turn survives its client (`detach`), no `Last-Event-ID` |
   | Live tree | `GET /orgs/{org}/activity`, `GET /orgs/{org}/activity/stream` (`activity.upsert`, `activity.ended`); `GET /runs/{id}/events` (SSE, `after_seq`, `Last-Event-ID`); `GET /runs/{id}/context-breakdown`; `POST /runs/{id}/cancel`; `POST /work-items/{id}/actions` (`stop`); `POST /orgs/{org}/agents/{slug}/stop`; `POST /orgs/{org}/assistants/{slug}/stop` | **G3** conversation turns are not listed yet (`kind=turn` answers nothing until S64-09c). **G4** a session's opening and closing are not on the bus (S64-05). **G5** no sub-agent: the journal holds each ACP `session/update` verbatim, but nothing says which tool call delegated to a sub-agent and which calls are its own (§8). **G6** an entry names its work item by key only (`work_item_title` missing). **G7** tokens per sub-agent: the gateway counts per run key, not per sub-agent (a non-goal, §10) |
   | Decisions | `POST /work-items/{id}/decisions`; `GET /orgs/{org}/actions?status=pending_approval`, `POST /projects/{id}/actions/{action_id}/decision`; `GET /me/conversations/{id}/actions`, `POST …/actions/{action_id}/confirm|refuse` | **G8** no list of what waits for the person across projects and conversations (the MCP door's `list_pending_decisions` is per project, human requests have no REST list). **G9** a developer surface has no sign-in that carries an authentication time (§6). **G10** the rule of §7 is not enforced: a pasted `*` token confirms a conversation's write and decides a human step today |
   | Elsewhere | `GET /me/inbox`; `GET /work-items/{id}/artifacts`, `…/{ref}`, `…/{ref}/content`; `GET /runs/{id}/diff`; `GET /me/conversations` (last activity) | none for a first version; which surface a conversation was last active on is not recorded (left out) |
   | Status line | the activity list (running); `GET /orgs/{org}/costs?since=&until=` (today, organisation readers only) | G8 (waiting for you); a project-only member's own spend today has no route (shown "—") |

5. **Sub-agents and tool calls are the platform's to name, not the client's.** A sub-agent launched inside a
   sandboxed run (Claude Code's `Task`/`Agent` tool, opencode's `task`, any ACP CLI that delegates) appears in
   the run's journal only as ACP `tool_call` updates. The platform derives the sub-agent nodes server-side,
   from a per-backend rule kept beside the backend's adapter (the delegating tool's name, the parent id the
   backend sends in `_meta` when it sends one), proved against recorded transcripts, and serves them with the
   run (G5); a surface never parses a backend's private shapes. A backend that sends no parent id shows its
   sub-agent's tool calls flat under the run, never guessed into a sub-agent. Tokens and cost stay per run
   (G7); the context breakdown of S64-08b stays per model call.

6. **A developer surface signs in as the person.** `choregos login` (and the editor's sign-in) obtains a
   **developer-surface session**: a credential the platform issues after an interactive sign-in of the person
   through their organisation's identity provider (ADR 0108), by a device-code flow brokered by the console
   (`POST /auth/device`, the console page `/device`, `POST /auth/device/token`), or by an authorisation code
   with PKCE to a loopback or editor URI when the surface can receive one. The session carries the person's
   subject, the sign-in's time (`auth_time`) and a session id; it is listed with the person's browser sessions,
   revoked from My account or by `choregos logout`, expires on inactivity, and is stored in the profile with
   mode `0600`, never passed as an argument. `choregos login --fresh` signs in again to meet a step-up
   (`step_up_required`). A pasted API token (`chg_`, scope `*`) keeps working for everything but §7, so no
   script breaks except a deciding one.

7. **A decision or a write confirmation from a developer surface is accepted only with the person's own
   session** (amends ADR 0139 §5, which reserved inline confirmation to the console and the embedded
   component). Confirming or refusing a write or a launch an assistant proposed, deciding a human step,
   approving or rejecting a governed action, approving a release: each is accepted from a principal of kind
   `user` that holds a session with an authentication time — a console cookie, an embedded frame token, or a
   developer-surface session of §6 — and the freshness the rule asks for (ADR 0035), and refused
   (`decision_requires_session`) for an API token, an MCP or A2A token, a machine account (ADR 0131), a run
   token and an outpost. The surface shows what the console's card shows (operation, readable arguments, the
   source's diff) before the gesture, and a confirmation is one explicit gesture per action, never a batch and
   never a default. The MCP door still decides nothing (ADR 0030 §5). Refusing the API token where it decides
   today (G10) is announced in the upgrade notes one minor version ahead and lands behind a deployment setting
   that may keep the old behaviour for that one version.

8. **The terminal uses Textual for the workspace and Rich elsewhere.** `ask` and `activity` need only Rich,
   already in the binary. `chat` uses Textual (MIT, by Rich's authors, on Rich's renderables): one rendering
   model for the whole CLI, a test pilot (`App.run_test`) and SVG snapshot tests (`pytest-textual-snapshot`,
   MIT, a test dependency only). Textual is imported only by `chat`, so `choregos --version` and every other
   command start as fast as today; the native build adds `--include-package-data=textual` and the binary's
   smoke test starts the workspace headless and quits it; the size budget of ADR 0151 §4 holds, and moving it
   is a reviewed change of its own. `choregos self update` is unchanged: the workspace ships in the same
   binary, at the instance's version.

9. **Colour orients, and is never alone** (ADR 0120 carried to the terminal):
   - status is a glyph and a word, then a colour: `● running` blue, `◐ queued` blue, `⏸ waiting for you` amber,
     `✓ done` green, `✗ failed` red, `■ stopped` grey; the live tree's frame is blue, the decisions' amber; an
     identity (a workflow) takes its hue from the same stable hash as the console's `workflowHue`, never red;
     the accent is for focus and selection only;
   - the terminal's 16 ANSI colours by default, so the person's own theme sets the contrast; no background is
     painted; dim is never used for something that must be read;
   - `NO_COLOR` (any non-empty value), `TERM=dumb`, output that is not a terminal, or `--no-color` give a
     monochrome rendering that loses nothing (glyphs and words carry it); an encoding that cannot draw the
     glyphs falls back to ASCII (`*`, `o`, `!`, `+`, `x`, `#`); `FORCE_COLOR` forces colour;
   - a test proves that every status renders a word, and that `NO_COLOR` output holds no escape sequence.

10. **Non-goals.** No local tool, model, agent or file edit (§1); no offline mode; no administration screens
    (installing an assistant, binding needs, budgets, members stay in the console); no decision through MCP;
    tokens per sub-agent; a mouse-first interface; the legacy Windows console without virtual terminal
    sequences (best effort).

## Alternatives rejected

- **A local coding agent in the CLI** (tools that run on the person's machine): it exists elsewhere, and it is
  what the platform governs, not what it is.
- **Letting a pasted API token confirm and decide** from the terminal: a long-lived credential with no
  authentication time would approve in the person's place from any script or stolen profile; ADR 0030 and ADR
  0035 already refuse it on the MCP door and for governed actions.
- **The surface parsing each backend's journal to find sub-agents**: every surface would re-implement, and
  disagree on, private shapes that change with each CLI release.
- **prompt_toolkit or urwid**: a second rendering model beside Rich, and no snapshot testing; **curses**: no
  Windows; **a second CLI in Go or Rust**: drops the core's offline validation, as ADR 0151 noted; **a local
  web page**: that is the console.
- **The OIDC flow of each organisation's provider run directly by the CLI only** (authorisation code with a
  loopback redirect): every provider must register a public client and a loopback URI, and it fails over SSH;
  the console-brokered device flow works wherever a browser exists, and PKCE stays available to a surface that
  can receive a redirect.

## Consequences

- Two first stories ship on routes that exist (`ask`, `activity`); the workspace follows; decisions from the
  terminal wait for the sign-in (G9), the enforcement (G10), the pending list (G8) and, for launches, ADR 0153.
- The contract grows by the gaps of §4, each through a `contract-change` pull request (ADR 0001).
- A person who scripted `choregos items approve` with a pasted token signs in with `choregos login` once a
  minor version after the notice.
- The editor extension (ADR 0155) reuses the screen model, the routes, the sign-in and the decision rule, and
  adds only its views.
