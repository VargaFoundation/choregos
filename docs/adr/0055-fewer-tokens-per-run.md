# 0055 — Fewer tokens per run: rtk and headroom, inside the runner pod, off until measured

- **Status**: accepted, 2026-10-13
- **Concerns**: the policy contract (`Policy.context`), the stage input, the runner image and its
  backends, the eval matrix; keeps [ADR 0004](0004-cout-compte-au-gateway.md) (the gateway
  meters every call) and [ADR 0005](0005-adaptateurs-et-fakes.md) (a scriptable fake per integration)

## Context

An agent spends most of its input tokens re-reading what tools gave it: shell output, file reads,
test logs, its own history. Nothing in Choregos reduces that today except blunt cuts: the DoD keeps
the head and tail of a check's output (`dod.py:_truncate`, 4,000 characters) and the MCP gate cuts a
result at 60 kB. The cost is metered at LiteLLM; the eval matrix scores results, not tokens.

Two open-source tools reduce it at two different places:

- **rtk** (Rust, a single binary, MIT) rewrites a shell command so its output is filtered before the
  agent reads it (`git status`, `pytest`, `cargo test`…). It hooks Claude Code's `PreToolUse` on
  `Bash`. It is lossy by design, sees only Bash calls, and keeps no state between runs.
- **headroom** (Python, Rust and an ONNX model, Apache-2.0) is a proxy between the agent and the
  model API: it compresses tool results and history (JSON crushing, AST-aware code compression, a
  small learned compressor) and keeps the originals so the model can ask for them back
  (`headroom_retrieve`, answered by the proxy itself). About 20 % fewer input tokens on coding
  sessions is the order of magnitude its authors publish; we have not measured it.

Re-implementing headroom's pipeline would take months; what matters is where it runs. Its own
security model says it is **not multi-tenant and not an isolation boundary**, and its retrieval
store holds plaintext originals.

## Decision

1. **Integrate both, re-implement neither.** rtk ships pinned in the runner image, its digest
   checked at build time (amd64: the static release binary; arm64: the release built from
   source); headroom as a pinned package in its own virtual environment there, its model
   downloaded at build time, never at run time.
2. **headroom runs inside the runner pod, one process per run, on loopback only**, stateless,
   offline (`HEADROOM_OFFLINE=1`), beacon off. A shared proxy is refused: it would hold several
   projects' plaintext in one process. The pod is the isolation, and it holds on every executor.
3. **The chain is agent → headroom (127.0.0.1) → LiteLLM → provider.** The run's virtual key passes
   through unchanged; LiteLLM still meters what is really sent, retrievals included (ADR 0004).
   `headroom wrap` is not used: it edits the agent's configuration and installs tools.
4. **Off by default, three switches, the strictest wins:**
   - the deployment: `CHOREGOS_CONTEXT_REDUCTION=off` (Helm `choregos-orchestrator.runner.contextReduction.enabled`)
     turns both off whatever a policy says;
   - the policy: `Policy.context` — `shell_filter: none | rtk`, `compression: none | headroom`,
     `headroom_profile`, `on_unavailable: bypass | fail`, `rtk_exclude_commands`, and `per_role`
     overrides; an absent `context` is everything off;
   - the stage: `StageInput.context` carries the resolved values, so a run says what it ran with.
5. **A run that loses its proxy says so.** `on_unavailable: bypass` (the default) talks to the
   gateway directly and journals `context.degraded`; `fail` fails the stage.
6. **What headroom reports is inferred, the cost is observed** ([ADR 0045](0045-la-provenance-d-un-signal.md)):
   its savings figures are journaled as an inferred signal; the real cost stays the ledger's.
7. **Default-on only after measurement, per backend.** The eval matrix gains a `context` dimension
   (`none`, `rtk`, `headroom`, `rtk+headroom`). A backend turns a reduction on by default only when
   its cells show a lower cost **and** a success rate that is not lower, within the matrix's
   confidence interval. Until then nothing changes for a project that does not ask.

## Consequences

- The runner image grows (headroom, its model, its native libraries); if the size or a CVE gate
  costs too much, a `runner-headroom` image variant carries it instead.
- rtk sees only Bash: Claude Code's Read, Grep and Glob bypass it. A repository's own
  `.claude/settings.json` wins over ours, as it already does for the scope hook.
- A lossy filter can hide the line an agent needed: the guardrails still judge the command the agent
  wrote, not the rewritten one, and `rtk_exclude_commands` and `per_role` let a policy keep raw
  output where it matters (a reviewer, a security stage).
- Not decided here: a context reduction for the other backends' native hooks (after the matrix), and
  any gain figure — none is claimed before the matrix has run on real models.
