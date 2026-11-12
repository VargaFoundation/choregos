# Choregos for Visual Studio Code

Work through your Choregos platform from the editor: what runs on it — work items, workflow stages, agents —
what it spends, and what waits for you.

**Nothing runs on your machine.** The extension is a surface of the platform
([ADR 0155](https://github.com/VargaFoundation/choregos/blob/main/docs/adr/0155-the-editor-is-a-surface-of-the-platform.md)):
it calls your instance's API under your own identity and shows what the platform serves. Agents, tools and models
run in the platform's sandbox, under its budgets, audit and approvals.

## Settings

- `choregos.instanceUrl` — your instance's address, as you open its console.
- `choregos.organisation` — the organisation the views show.

## Privacy

No telemetry. The extension talks to the instance you configure, and to nobody else.

Apache-2.0, © the Varga Foundation.
