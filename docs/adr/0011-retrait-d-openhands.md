# 0011 — OpenHands removed

- **Status**: accepted, 2026-09-21
- **Supersedes**: OpenHands as the default backend (`docs/plan/02-orchestrateur-agents-runner.md`)

## Context

The plan made OpenHands the default backend, launched as `openhands acp`. Confronted with
its real binary in the runner image, that launch does not exist:

- **0.59** only has two subcommands, `serve` and `cli`; `openhands acp` silently falls back
  to `cli`;
- **1.x** no longer has an `openhands` binary at all. Its entry point is `agent-server`, an
  HTTP server, and the `openhands` module is no longer importable.

The plan half-said it ("`openhands acp` **or** `agent-server` + an ACP client"). The code
had taken the first branch as a fact, and the conformance suite passed because it runs
against fakes. A project provisioned with the default therefore failed on its first ticket.

OpenHands was also the heaviest part of the image: its Python tree carried four of the
fourteen CRITICALs reported by Trivy (`litellm`, `fastmcp`, `anyio`, `GitPython`, including
a remote code execution), and it is what made the arm64 build interminable under emulation.

## Decision

OpenHands is removed.

- It is no longer installed in the `choregos-runner` image, nor its transitive dependencies.
- It is no longer in the backend registry. `get_backend("openhands")` **refuses it with its
  reason** (`RETIRED` in `choregos_runner.backends`) rather than with an "unknown backend"
  that would look like a typo.
- The default becomes `claude-code` everywhere it was written: contract schema and model,
  templates, model resolver, evals, and the `KNOWN_BACKEND_NAMES` preference order, which
  put OpenHands **first**.

Still installed and called when the image is built: `claude-code-acp`, `gemini` (`--acp`)
and `opencode` (`acp`).

## Consequences

- A project with `default_backend: openhands` in its configuration fails on its first run
  with a message that says why and what to migrate to.
- `claude-code` only accepts Claude models, a hard constraint. But the orchestrator does not
  load the gateway catalogue: a `platform/*` alias is never resolved there, and the
  constraint refused it **always** — every stage failed at `prepare_stage`. On an
  unresolved alias the constraint therefore becomes a warning; it stays blocking as soon as
  the real model is known. The platform gateway must route the aliases a `claude-code`
  project uses to Claude models.
- The runner image loses OpenHands' Python tree: lighter, faster to build, four fewer
  CRITICALs to watch.

## To come back to it

Two conditions, in this order: that OpenHands exposes an ACP agent (or that an ACP client
to its `agent-server` is written), then that the backend passes the conformance suite
**against the real binary** in the image, not against a fake. The removed adapter is in the
history (`packages/runner/src/choregos_runner/backends/openhands.py`, before that commit).
