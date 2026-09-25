# 0022 — A federable catalogue: proposals from registries, merged by a human

- **Status**: proposed, 2026-09-25 — not implemented
- **Concerns**: `choregos_core.catalogue`, `choregos-admin`, the GitOps repository of a project

## Context

ADR 0014 gave the platform a tool catalogue it holds for the agent: HTTP and external MCP
tools, key server-side, exposed per group, with **no auto-discovery** — "what is
deliberately absent" lists it, on purpose. The consequence is a catalogue of what someone
typed. Meanwhile treg indexes thousands of endpoints and the MCP registry publishes a
`server.json` per server; Obot and Docker's MCP gateway broker them at runtime. None of
them hides the real name or refuses discovery (positioning page); all of them have breadth.

The breadth is worth having. The runtime discovery is not: a tool that appears in an agent's
list because a registry said so is a supply-chain path into every run.

## Decision

1. **Federation is a proposal, never a mount.** `choregos-admin catalogue propose <source>`
   reads a registry entry (MCP registry `server.json`, an OpenAPI document, a treg export),
   renders it as `OutilCatalogue` entries and opens a **pull request** on the project's
   GitOps repository (ADR 0009). Nothing reaches an agent before a human merges.
2. **A proposed tool is born closed.** `groups: []` (nobody sees it), `credential_env`
   empty (it cannot call anything), and a `provenance` block: source, entry hash, date, the
   admin who ran the command. Opening it to a group is a second, deliberate diff.
3. **The CI check on the catalogue** loads it with `charger_catalogue`, refuses any `url`
   outside the project's allowed egress domains (P1-5 proxy), refuses an input schema that
   is not a JSON Schema, and diffs the entry against its source hash so a silently changed
   upstream shows up as a change, not as trust.
4. **The registry is read through the egress proxy** with the platform's identity, from the
   API, never from a runner.

## Conditions

- The MCP registry's `server.json` maps to `AppelMcp` (`url`, `tool`, `headers`); an
  OpenAPI operation maps to `AppelHttp` (`method`, `url`, `query`, `body`). Anything the
  mapping cannot express (streams, binary bodies) is refused with the reason, not
  approximated.
- Tool names keep the catalogue's pattern (`^[a-z][a-z0-9_]{2,63}$`); collisions with the
  eleven built-in tools are refused.

## Consequences

- Breadth without a runtime dependency on any registry: the catalogue is a file, reviewed,
  versioned, replayable.
- The access report (ADR 0014) stays complete: every callable tool has a merged commit and
  a person behind it.

## Alternatives discarded

- **A runtime MCP gateway** (Obot, Docker MCP Gateway) in front of the sidecar: brokers give
  the agent the real server names and the discovery we refuse; they solve the wrong problem
  for a governed run.
- **Trusting a registry's signature** as sufficient for exposure: a signed entry proves who
  published it, not that this project should let an agent call it.
