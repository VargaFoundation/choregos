# Choregos for Visual Studio Code

Work through your Choregos platform from the editor: what runs on it — work items, workflow stages, agents —
what it spends, and what waits for you.

**Nothing runs on your machine.** The extension is a surface of the platform
([ADR 0155](https://github.com/VargaFoundation/choregos/blob/main/docs/adr/0155-the-editor-is-a-surface-of-the-platform.md)):
it calls your instance's API under your own identity and shows what the platform serves. Agents, tools and models
run in the platform's sandbox, under its budgets, audit and approvals.

## What it shows

- **Live** (in the Choregos view of the activity bar): what runs on your instance — project, work item, then each
  run, agent session or conversation turn, with its state (an icon, a word and your theme's colour), elapsed time,
  tokens and cost so far. A figure the platform does not know reads `—`, never 0.
- **The status line**: `N running · — waiting for you · $X today`. "Waiting for you" reads `—` until your instance
  can list it.

Sign in with **Choregos: Sign in** and your own API token (the console: My account → API tokens). It reads what
you may read; it decides nothing.

## Settings

- `choregos.instanceUrl` — your instance's address, as you open its console.
- `choregos.organisation` — the organisation the views show.

## Privacy

No telemetry. The extension talks to the instance you configure, and to nobody else.

Apache-2.0, © the Varga Foundation.
