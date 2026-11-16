# 0155 — The editor is a surface of the platform

- **Status**: proposed, 2026-11-12 (the product owner's request of the same day; the views of its mock-up
  approved the same day)
- **Concerns**: a new application `apps/vscode` (a Visual Studio Code extension, also installable in the
  editors that read the Open VSX registry), the CI job that builds it, the release that ships it, the instance
  that serves it; reuses the screen model, the routes and their gaps, the sign-in and the decision rule of
  ADR 0154 (the terminal, stream S67), which this decision does not repeat; builds on
  [ADR 0120](0120-colour-orients.md), [ADR 0129](0129-an-artifact-says-how-it-is-shown.md),
  [ADR 0134](0134-every-person-has-a-personal-space-conversations-are-durable.md),
  [ADR 0141](0141-tokens-and-the-context-are-read-per-scope.md),
  [ADR 0142](0142-a-stop-interrupts-what-is-in-flight.md) and
  [ADR 0151](0151-the-cli-ships-as-native-binaries.md); stream S68

## Context

The product owner (2026-11-12): the developer works *through* the platform instead of running a coding agent
on their machine. They converse with a Choregos assistant; work items and workflows run remotely; they see live
progress — work items, workflow stages, agents and the sub-agents those launch, tokens and cost — and the
decisions waiting for them. The terminal does this (ADR 0154). Most developers spend the day in an editor, and
the same surface belongs there, beside the code the platform's agents change.

ADR 0154 settles what every developer surface shares: a developer surface is a client of the REST API under
the person's identity and runs nothing locally (§1); one screen model of five parts — conversation, live tree,
decisions, elsewhere, status line (§2); the routes each part reads and the gaps G1-G10 the platform must close
(§4); sub-agents named by the platform, never parsed by a surface (§5); a developer-surface session obtained by
an interactive sign-in (§6); and a decision or a write confirmation accepted only under that session (§7).
What remains is how an editor draws those parts, signs in, ships, and stays out of the person's files.

Visual Studio Code has native primitives for most of the model — view containers, tree views with theme icons
and badges, a status bar, commands, authentication providers, secret storage, virtual documents and a diff
editor — and webviews for the rest. Its extension API is also what VSCodium, Cursor, Windsurf, Gitpod and
Eclipse Theia implement, through the Open VSX registry.

## Decision

1. **The editor is a developer surface (ADR 0154 §1), nothing more.** The extension calls the instance's REST
   API as the person and nothing else; it runs no tool, model or agent, holds no model key, and never writes in
   the person's workspace on its own. Its only local effects are opening read-only documents of what the
   platform produced, comparing two of them in the diff editor, and saving one where the person picks in a save
   dialog. It does not read the CLI's profile: the editor signs in on its own, and each sign-in is a session the
   person sees and revokes from My account.

2. **Native views where the editor has them, one webview where it does not.** The five parts of ADR 0154 §2:

   | Part | Editor primitive |
   |:--|:--|
   | Conversation | a webview view in the Choregos container (`choregos.conversation`), movable to the secondary side bar, and `Choregos: Open conversation in editor` for a webview panel |
   | Live tree | a tree view `choregos.live`: organisation's projects → work item → run (stage) → agent session → sub-agents and tool calls, as the platform serves them (ADR 0154 §5) |
   | Decisions | a tree view `choregos.decisions` whose badge counts what waits for the person |
   | Elsewhere | a tree view `choregos.elsewhere`: other projects' activity, conversations active elsewhere, artefacts delivered |
   | Status line | one status bar item: `N running · N waiting for you · $X today`, a click focuses the container |

   A Choregos view container in the activity bar holds the four views. Commands: `Choregos: Ask`, `Choregos: New
   conversation`, `Choregos: Stop run` (a reason always asked, ADR 0142), `Choregos: Open artefact`,
   `Choregos: Show diff`, `Choregos: Sign in`, `Choregos: Sign out`, `Choregos: Choose organisation`. Each tree
   item's context menu offers the commands that apply to it, and only when the platform says the person may
   (`can_stop`).

3. **A state is an icon, a word and a theme colour, never a colour alone** (ADR 0120). Each node carries a
   `ThemeIcon` coloured with a `ThemeColor`, so the person's theme — light, dark or high contrast — sets the
   contrast; the word is in the item's accessible label and tooltip:

   | State | Icon | Colour |
   |:--|:--|:--|
   | done | `pass` | `charts.green` |
   | running | `sync~spin` | `charts.blue` |
   | waiting for you | `bell` | `charts.yellow` |
   | queued | `circle-outline` | `disabledForeground` |
   | failed | `error` | `charts.red` |
   | stopped | `debug-stop` | `disabledForeground` |

   Queued is grey here, as the product owner's mock-up for the editor draws it; ADR 0154 §9 draws it blue. The
   two surfaces must agree before either ships its live view, and whichever changes says so in its ADR. A node's
   description is `elapsed · tokens · cost`; a figure the platform does not know is `—`, never 0 (ADR 0141 §2).

4. **The conversation webview is the extension's own page, locked down and themed by the editor.** Its content
   security policy is `default-src 'none'`, with the extension's one bundled script under a nonce and its one
   stylesheet; it loads nothing remote. Model text, citations and tool output are inserted as text nodes, and the
   little formatting the extension renders (paragraphs, lists, code blocks, links) is built element by element,
   never from HTML the model or a tool returned. Colours, fonts and focus rings come from the editor's
   `--vscode-*` variables, so the panel follows light, dark and high-contrast themes without a palette of its
   own. The webview holds no token: it posts the person's gestures to the extension, which calls the API. A
   conversation is the durable one of ADR 0134, so a hidden or reloaded panel is restored from the platform, not
   kept alive in memory. A proposed write or launch shows the operation, its readable arguments and the source's
   diff; its Confirm and Refuse buttons are offered only under a developer-surface session (ADR 0154 §6-7), one
   gesture per action, never a batch, never a default; under an API token the card offers *Open in the console*
   instead.

5. **The person signs in through an authentication provider.** The extension registers an
   `AuthenticationProvider` (`choregos`, *Choregos*), one account per instance address, so the editor's
   Accounts menu lists it and signs it out. It obtains the developer-surface session of ADR 0154 §6: an
   authorisation code with PKCE redirected to the editor's own URI (`vscode.env.asExternalUri`, which also works
   in a remote or browser-hosted editor), and the device-code flow when the editor cannot receive a redirect.
   Until the platform offers that session (ADR 0154 G9), the provider accepts the person's own API token of scope
   `*`, the credential `choregos login --token` stores today, checked with `GET /me`: it reads everything the
   person may read and stops what they may stop, and it decides nothing — the decision and confirmation gestures
   of §4 and §6 stay closed under it. A token of another scope (MCP, A2A) is refused with the API's own message.
   Every credential lives in the editor's `SecretStorage` only — never in settings, the webview, a log line or an
   error message. The instance's address and the default organisation are settings (`choregos.instanceUrl`,
   `choregos.organisation`); the API is reached only at that origin, and a redirect to another origin is not
   followed with the credential.

6. **Live data comes from the routes of ADR 0154 §4, through one typed client.** The client in `apps/vscode`
   is typed by the contract's generated types (`packages/contracts/ts`), so a contract change that moves a field
   fails the extension's typecheck. The live tree and the status line read `GET /orgs/{org}/activity` once and
   then follow `GET /orgs/{org}/activity/stream`, listing again after any reconnection (the stream has no
   resumption point); an expanded run follows `GET /runs/{id}/events` from its last `seq`; spend today reads
   `GET /orgs/{org}/costs` for the day. A stream that cannot be held falls back to listing every 30 seconds, and
   the status bar says the view is not live. Nothing the platform has not served is shown: until ADR 0154 closes
   its gaps, the editor shows what the routes give — no sub-agents before G5, "waiting for you" as `—` before G8,
   no confirmation before G9 and G10 — and says so in the view rather than guess.

7. **The extension ships with the platform, at the platform's version.** CI builds the VSIX on every change to
   `apps/vscode` and keeps it as a build artefact. The release attaches `choregos-vscode-<version>.vsix`, signed
   with cosign like the CLI's files (ADR 0151 §5); the API image carries it, and the instance serves it beside
   the CLI (`/downloads/vscode/`), so a person installs the version their instance runs, offline included. It is
   also published to Open VSX and to the Visual Studio Marketplace under the Varga Foundation's publisher, once
   the foundation holds those accounts. The extension reads the instance's version and says so when its own
   minor version differs.

8. **No telemetry.** The extension sends nothing to anyone but the configured instance and, during sign-in,
   the identity provider the instance names. It creates no telemetry logger and reports no usage, error or
   crash; its log is the *Choregos* output channel on the person's machine, without credentials. Its requests
   carry `User-Agent: choregos-vscode/<version>`, so My account says which client last used a credential.

9. **It is tested on fakes, like every client of the platform** (ADR 0005). The extension's logic sits in
   modules that do not import the editor's API; the views are thin adapters over them. Unit tests run under
   Vitest with the editor's API replaced by a scriptable fake module, and the platform replaced by an in-process
   fake API that answers the contract's shapes over real HTTP and server-sent events. An extension-host test
   (`@vscode/test-electron` under a virtual display) joins CI only if it runs headless within the job's budget.

10. **Non-goals.** A local coding agent, a local tool, a model call from the editor; applying a patch to the
    working tree (the platform pushes a branch, the person checks it out); inline completions; a chat
    participant in another vendor's chat view; administration screens (they stay in the console); an offline
    mode; the editor running in a browser (`vscode.dev`) in a first version; telemetry of any kind.

## Alternatives rejected

- **The whole surface in one webview** (the console's pages, or a single-page app): it brings back a second
  console, loses the editor's keyboard navigation, accessibility, theming and layout, and puts every credential
  in a page.
- **A chat participant in the editor's built-in chat**: it exists only where a vendor's chat extension is
  installed, answers with that vendor's model by default, and has no place for the live tree or the decisions.
- **The deprecated webview UI toolkit** (`@vscode/webview-ui-toolkit`, archived in 2025): the editor's own CSS
  variables give the same look with no dependency.
- **Driving the `choregos` CLI as a child process**: the extension would start a local process, depend on how
  the CLI is installed, and parse a human interface; the REST API is the contract.
- **Reading the CLI's profile to sign in**: one tool's credential silently reused by another, outside the
  editor's secret storage and outside the person's view of their sessions.
- **Publishing to the Marketplace only**: the editors built on Open VSX and the instances without internet
  access would have nothing to install.

## Consequences

- A first useful extension ships on routes that exist: the read-only live tree and the status line, then the
  conversation; decisions and confirmations wait for ADR 0154's sign-in (G9), its enforcement (G10) and the
  pending list (G8); sub-agents wait for G5.
- The repository gains a second TypeScript application with its own lockfile, CI job and coverage floor, and a
  release artefact more.
- The colour of *queued* is settled once for both developer surfaces.
- The extension is one more client of the contract: a path it is the only one to call is named in the API
  routes' guard (`tests/web/test_api_routes_have_a_client.py`) when the guard reads it.
