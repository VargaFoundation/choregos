# ADR-0002 — ACP as the agent contract, OpenHands by default

- **Status**: accepted — the OpenHands default is **superseded** by [ADR 0011](0011-retrait-d-openhands.md) (default: `claude-code`)
- **Concerns**: S2, S13

## Context

The coding-agent ecosystem moves fast and unevenly. Binding to one agent means binding to
its roadmap; supporting several without a common contract means one adapter per agent and
per version.

## Decision

The runner speaks **ACP** (Agent Client Protocol, JSON-RPC over stdio) and nothing else. A
backend boils down to a command line, environment variables for the model, and
configuration files. OpenHands is the default agent; Claude Code, Codex, Gemini CLI, Goose,
OpenCode and Copilot CLI are optional backends.

A **conformance suite** of seven checks guards the door (§2.2): start-up, MCP servers, a
trivial prompt, a refused permission that is not worked around, a valid `result.json`,
`max_turns` honoured, cost visible at the gateway. A backend that fails is **disabled
automatically** in `platform/backends` until fixed.

## Consequences

- Changing agent is a line of configuration, not a project.
- An agent that regresses is set aside without debate and without a production incident.
- We depend on a young protocol: the conformance suite is our net.

## Alternatives discarded

- **A single agent**: total dependence on its roadmap and its limits.
- **A home-made adapter per agent**: maintenance cost proportional to the number of agents.
