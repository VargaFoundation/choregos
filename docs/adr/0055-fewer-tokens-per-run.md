# 0055 — Fewer tokens per run: rtk and headroom, inside the runner pod, off until measured

- **Status**: accepted, 2026-10-13
- **Concerns**: the policy contract (`Policy.context`), the stage input, the runner image and its
  backends, the eval matrix; keeps [ADR 0004](0004-cost-counted-at-the-gateway.md) (the gateway
  meters every call) and [ADR 0005](0005-adapters-and-fakes.md) (a scriptable fake per integration)

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
   source); headroom as a pinned package in its own virtual environment there, every wheel
   checked against its hash, its model downloaded at build time at the revision headroom pins,
   never at run time.
2. **headroom runs inside the runner pod, one process per run, on loopback only**, stateless,
   offline (`HEADROOM_OFFLINE=1`, and `LITELLM_LOCAL_MODEL_COST_MAP` for the litellm it imports),
   beacon off. A shared proxy is refused: it would hold several
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
6. **What headroom reports is inferred, the cost is observed** ([ADR 0045](0045-the-provenance-of-a-signal.md)):
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
- rtk rewrites the command before Claude Code asks the runner for permission, so the guardrails
  receive the rewritten one. They judge it, then every form the agent may have written (`rtk`
  removed, `rtk read` read as `cat`, `head` and `tail`), and refuse if any form is refused. rtk's
  configuration, under the run's `$HOME`, also excludes every command the policy forbids, so
  none of them is rewritten. No `permissions.allow` rule is written: with one, rtk would grant the
  permission itself.
- The runner starts headroom for the run, before the agent: `headroom proxy --stateless` on a free
  loopback port, every upstream it knows set to the gateway, an environment built from nothing (no
  run token, no key, offline), `$HOME` and working directory outside the workspace. It waits on
  `/livez` (60 s), points the agent's base URL at it, and stops it with the run, whatever the run's
  end. A proxy that does not answer is journaled `context.degraded`; with `on_unavailable: fail`
  the stage fails with exit code `80`, before any model call. Only the Anthropic and OpenAI
  formats, and only a run with a gateway key, go through it.
- A run says which reduction it ran with, in its journal: what it was given (`context.reduction`),
  whether its backend wired the shell filter (`context.shell_filter`, `applied: false` on a backend
  without a hook), whether headroom ran and in which version (`context.compression`) or not
  (`context.degraded`), and what headroom says it saved (`context.savings`). The savings are read
  from `/stats` once the agent is done: requests, requests compressed, tokens before and removed,
  originals asked back. They are headroom's count, before the gateway, so they are **inferred**
  (ADR 0045), and its dollar figures are left out. The run page shows them as such, next to the
  gateway's cost, which stays the observed one. ADR 0015's attestation is not implemented; when it
  is, it reads these events.
- The eval matrix has a context dimension: `EvalMatrix(contexts=[...])` among `none`, `rtk`,
  `headroom`, `rtk+headroom`, default `["none"]`, so a matrix started before it keeps its commands
  and replays. Each ticket of a cell is played under its own gateway key, revoked after; the cell's
  input (cache reads included), cache-read and output tokens and its cost are medians of what the
  gateway's ledger says those keys spent, over the tickets it has a measure for (`metered`). A
  ticket nothing went through has no measure, never a guess. Turns are not in the ledger: they come
  from the run's diagnostics. In CI the tickets' `scripted_usage` is billed on the fake gateway, so
  the fake matrix proves the plumbing, not a gain. The scripted matrix runs no agent, so on a real
  gateway its ledger stays empty; the live cells below do. A backend is validated by its cells
  without reduction only: a run gets none unless its policy says so.
- Live cells (`EvalMatrix(live=true)`) play each ticket as a real stage on the eval project's
  executor, the path a work item takes: `seed_eval_ticket` commits the toy repository to
  `choregos-evals/<repo>-<digest>` in the eval project's repository and files an eval work item
  (one per matrix run, backend, model, context and ticket, so a retry finds it); the workflow then
  runs `prepare_stage`, `start_run`, `await_run`, `record_run_outcome` and, whatever the run did,
  `collect_spend`; `judge_eval_run` gives the verdict. The stage plan carries the cell
  (`StagePlan.eval_cell`): its reduction replaces the policy's, never the kill switch's; the toy
  branch is the base branch; the ticket's assertions become the run's fact commands, which the
  runner measures in its workspace once the agent is done. A case succeeds when every fact is true
  and the run blocked exactly when the ticket expects it; its tokens and cost are the run's ledger
  row. A headroom cell asks `on_unavailable: fail`, and a run that did not get the cell's reduction
  (another reduction in its stage input, `context.degraded`, a shell filter its backend did not
  wire) is skipped, never counted: it would credit the reduction with a run made without it. A
  ticket whose tool is missing on the runner (`requires`) is skipped too. The eval project needs a
  repository, the internal tracker and memory off; `with_memory: true` is skipped (memory is not
  wired in live cells). A failing fact reaches the agent through the runner's repair loop, as a
  project's facts do: every context gets the same loop, so the comparison holds, but a live success
  rate is "with the checks' feedback", not a blind one.
- A lossy filter can hide the line an agent needed: `rtk_exclude_commands` and `per_role` let a
  policy keep raw output where it matters (a reviewer, a security stage).
- Not decided here: a context reduction for the other backends' native hooks (they have not been
  measured).

## Measurement (2026-10-19, S37-10)

The live matrix `evals-live-20261019b` ran on the dev tenant (Choregos 0.18.7): backend
`claude-code`, model `platform/claude-sonnet` (Claude Sonnet 5.5), memory off, the four contexts,
the six reference tickets each, so 24 real runs. Every case was metered; none was skipped.

| Context | Success | Median cost | Mean cost vs `none`, paired (95 % CI) | Median cache read | Median cache write | Median uncached input | Median output |
|:--|:--|--:|:--|--:|--:|--:|--:|
| `none` | 5/6 | $0.224 | — | 329,568 | 49,786 | 2,556 | 2,958 |
| `rtk` | 5/6 | $0.232 | −3 % (−$0.024 … +$0.015) | 330,083 | 50,194 | 2,556 | 3,025 |
| `headroom` | 5/6 | $0.324 | **+43 %** (+$0.083 … +$0.117) | 251,707 | 92,618 | 8,572 | 2,960 |
| `rtk+headroom` | 5/6 | $0.316 | **+47 %** (+$0.081 … +$0.133) | 231,484 | 92,112 | 8,400 | 2,853 |

- **How the cost was computed.** The tokens are the gateway's spend logs of each run's key
  (observed). The gateway's own cost could not be used: neither the dev LiteLLM nor the platform
  gateway gives a price to cache reads and writes, so it counted $1.01 for the 24 runs, mostly
  output. Each case is priced here at the platform gateway's rates for this model ($2 per million
  input tokens, $10 per million output tokens), with Anthropic's cache ratios (a cache read 0.1×
  the input price, a cache write 1.25×): $6.78 for the 24 runs. The ratios are an assumption about
  the provider's bill, not read from it. The confidence interval is a bootstrap of the mean paired
  difference, ticket by ticket, over the six tickets.
- **Where the money goes.** About 85 % of a run's input is read from the provider's prompt cache.
  headroom compresses tool results and history, and that changes the prompt's prefix from one
  request to the next. The cache is then written again: about 92 k tokens written per run instead
  of 50 k, and three times as many uncached tokens. The tokens it removes are mostly cache reads,
  the cheapest kind. rtk changes the Bash output only, and its effect is within the noise on these
  toy repositories, whose command outputs are short.
- **Success.** 5/6 in every cell. The one failure is the same ticket everywhere
  (`py-hors-perimetre`): the agent asks a human (`needs_human`) where the ticket expects
  `blocked`. It does not separate the contexts.

**Decision for `claude-code`: no reduction on by default.** headroom costs more, outside the
interval. rtk does not show a lower cost: its interval contains zero. `Policy.context` stays off
unless a project asks for it.

**Not measured, so not decided:** the other backends; other models; real repositories, where
command outputs and files are longer and rtk may count; headroom with a mode that keeps the cache
prefix stable. Six tickets per cell can show a 43 % cost difference, not a few points of success
rate. The ledger did not count cache reads during this matrix (S37-16).
