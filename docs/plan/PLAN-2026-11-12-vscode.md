# Plan — 2026-11-12: the editor is a surface of the platform (stream S68)

Source: the product owner's request of 2026-11-12: the developer works *through* the platform instead of running a
coding agent locally — they converse with a Choregos assistant, work items and workflows run remotely, and they
see live progress (work items, stages, agents and their sub-agents, tokens and cost) and the decisions waiting for
them, inside their code editor. The views of the mock-up were approved the same day: a conversation pane; a live
tree run → stage → agent → sub-agents with status colours (done green, running blue, waiting amber, queued grey),
elapsed time, tokens, cost; a decisions pane answered in one action, signed as the person; *Elsewhere*; a status
line `N running · N waiting for you · $ today`.

Checked against `main` at `36a8cade`. Stream number: **S68**. ADR: **0155** (proposed). The terminal surface of the
same day (stream S67, ADR 0154) defines the screen model, the routes each part reads, the platform's gaps G1-G10,
the developer-surface sign-in and the decision rule; this plan reuses them and adds only what an editor needs.

Status words: **exists** (in the code), **missing**, **depends** (on a story of another stream).

## 0. What the audit found

| What the editor needs | Where | Today |
|:--|:--|:--|
| What is in flight, as a list and a stream | `GET /orgs/{org}/activity`, `…/activity/stream` (S64-05) | **exists** |
| A run's steps | `GET /runs/{id}/events` (JSON or SSE, `after_seq`, `Last-Event-ID`) | **exists** |
| Spend today | `GET /orgs/{org}/costs?since=&until=` (organisation readers) | **exists** |
| Stop a run with a reason | `POST /runs/{id}/cancel` (S64-03) | **exists** |
| A run's context composition | `GET /runs/{id}/context-breakdown` (S64-08) | **exists** |
| Durable conversations, a streamed turn, cancel | `/me/conversations/*` (ADR 0134) | **exists** |
| Citations and confirm links on a REST turn | ADR 0154 G1 | **depends** (S67) |
| Sub-agents named by the platform | ADR 0154 G5 | **depends** (S67) |
| What waits for the person, across projects | ADR 0154 G8 | **depends** (S67) |
| A sign-in with an authentication time | ADR 0154 G9 (`POST /auth/device`, PKCE) | **depends** (S67) |
| Decisions refused to an API token | ADR 0154 G10; `services/actions.py` refuses already for governed actions | **depends** (S67) |
| Artefacts and a run's diff | `GET /work-items/{id}/artifacts…`, `GET /runs/{id}/diff` | **exists** |
| The person's own token, checked | `GET /me` with `Authorization: Bearer` (scope `*`) | **exists** |
| A second TypeScript application in CI | `.github/workflows/ci.yml` (`web` job), `Makefile` (`web-ci`) | **missing** for an editor |
| The instance serving a client it bundles | `routers/cli_downloads.py` (S62-08) | **exists** for the CLI only |

## 1. Stories

Order of the pull requests: S68-00, S68-01, S68-02, S68-03 (the first useful slice: a read-only live tree and the
status line), then S68-04 and S68-05; S68-06, S68-07 and S68-08 once their ADR 0154 gaps are on `main`; S68-09 and
S68-10; S68-11 to S68-14 last.

### S68-00 · docs(adr): ADR 0155, this plan, the backlog stories

**Accepted when** the ADR index lists 0155 as proposed, the backlog holds S68-00..14 and STATUS has their rows.

### S68-01 · build(vscode): the extension's scaffold, its typed client and fake API, built in CI

`apps/vscode`: strict TypeScript, ESLint, Prettier, Vitest with a coverage floor, an esbuild bundle and a VSIX;
the API client typed by `@choregos/contracts`, a scriptable in-process fake API (JSON and server-sent events), the
editor's API replaced by a fake module in tests; the `vscode` CI job and `make vscode-ci`; Dependabot follows the
lockfile. **Accepted when** `make vscode-ci` is green, the CI job uploads the VSIX, the client sends the bearer
only to the configured origin and refuses to follow a redirect elsewhere, an API problem (RFC 9457) surfaces as its
`detail`, and the server-sent-event reader yields the fake's events in order across chunk boundaries; each red
with its guard reverted.

### S68-02 · feat(vscode): the person signs in, through an authentication provider (ADR 0155 §5)

`AuthenticationProvider` `choregos`, one account per instance; until ADR 0154 G9, the person's own API token
(scope `*`) checked with `GET /me`; stored in `SecretStorage` only; sign-out forgets it. **Accepted when** a token
the fake API accepts makes a session named by the person's email, a token it refuses (401) or a token of another
scope (403) makes none and shows the API's message, sign-out deletes the secret, and no settings key, log line or
error message ever holds the token.

### S68-03 · feat(vscode): the Live tree and the status line, read-only (ADR 0155 §2-3, §6)

The `choregos.live` view: projects → work items → entries (runs, sessions, turns) from `GET /orgs/{org}/activity`,
kept live by its stream, listed again after a reconnection; each node an icon, a word and a theme colour, described
`elapsed · tokens · cost` with `—` for unknown; the status bar item `N running · — waiting for you · $X today`.
**Accepted when** a stream `activity.upsert` adds or moves a node and `activity.ended` turns it done, failed or
stopped, unknown spend reads `—` and never `0`, every state has a word in its accessible label, a lost stream falls
back to listing and says so, and "waiting for you" is `—` until G8.

### S68-04 · feat(vscode): a run's steps, its context, and stopping it

Expanding a run follows `GET /runs/{id}/events` from its last `seq`; its tooltip shows the context breakdown;
`Choregos: Stop run` asks a reason and calls `POST /runs/{id}/cancel`, offered only when `can_stop`. **Accepted
when** a reconnection resumes at `Last-Event-ID` without a duplicate step, an empty reason sends nothing, and a 409
says the run had already ended.

### S68-05 · feat(vscode): the conversation panel (ADR 0155 §4)

A webview view: assistants, conversations listed and resumed, a new conversation, a streamed answer, cancel;
`Choregos: Ask` (with the selection only when the person chose *Ask about the selection*, which says the text
leaves the machine). **Accepted when** the page's CSP is `default-src 'none'` with a nonce, a model answer holding
`<script>` or `<img onerror>` renders as text, the panel restored after a reload shows the platform's history,
closing the stream cancels the turn, and the page holds no token.

### S68-06 · feat(vscode): citations and proposed actions in the conversation

Depends on ADR 0154 G1. Citations open their source; a proposed write or launch shows its operation, arguments and
diff, with *Open in the console* under an API token. **Accepted when** each pending action renders its card and
its link, and no Confirm button exists under an API token.

### S68-07 · feat(vscode): sign in with a developer-surface session (ADR 0154 §6)

Depends on ADR 0154 G9. PKCE to the editor's URI (`vscode.env.asExternalUri`), the device-code flow otherwise; a
refresh before expiry; `step_up_required` signs in again. **Accepted when** a state mismatch or a wrong verifier
makes no session, the device flow polls at the interval the instance gives, and the session is revoked on sign-out.

### S68-08 · feat(vscode): the Decisions view, answered in one action, signed as the person

Depends on ADR 0154 G8, G9, G10. The `choregos.decisions` view and its badge; approve, reject (a reason), answer,
confirm or refuse a proposed write, each one gesture per item under the person's session; the conversation's
Confirm and Refuse buttons. **Accepted when** the badge equals the pending count, a refused decision shows the
API's reason, and under an API token no decision gesture is offered.

### S68-09 · feat(vscode): Elsewhere

Other projects' activity, conversations active elsewhere, artefacts delivered (`GET /me/inbox`). **Accepted when**
an entry of a project outside the editor's focus appears there and not in Live.

### S68-10 · feat(vscode): open an artefact, show a diff (ADR 0155 §1)

A read-only `choregos-artifact:` document per revision (`…/artifacts/{ref}/content`, digest checked by the
platform); `Choregos: Show diff` compares two revisions in `vscode.diff`; *Save as* writes only where the person
picks. **Accepted when** an artefact document cannot be saved in place, the diff opens with both revisions, and no
command writes in the workspace without a save dialog.

### S68-11 · build(release): the VSIX in the release, signed, served by the instance (ADR 0155 §7)

The release attaches `choregos-vscode-<version>.vsix` with its cosign bundle; the API image carries it; the
instance serves it under `/downloads/vscode/` beside the CLI. **Accepted when** the release workflow's guard test
names the VSIX and its signature, and the download route serves only the file the bundle lists.

### S68-12 · build(release): publish to Open VSX and the Visual Studio Marketplace

Blocked on the Varga Foundation's publisher accounts (BLOCKERS). **Accepted when** a tag publishes both from the
release's signed VSIX, never from a rebuild.

### S68-13 · test(vscode): an extension-host smoke under a virtual display

`@vscode/test-electron`: the extension activates, registers its views and commands, and reads the fake API.
**Accepted when** it runs in the `vscode` job within ten minutes; otherwise the story records why it was dropped.

### S68-14 · docs: the editor in the user documentation

Install (from the instance, Open VSX, the Marketplace), sign in, the views, what never runs locally, no telemetry.

## 2. What this plan does not do

- No local agent, tool or model call; no patch applied to the working tree.
- No chat participant in another vendor's chat view, no inline completion.
- No editor-in-a-browser build in a first version.
- No decision before ADR 0154's sign-in and enforcement; no sub-agent before the platform names them.
