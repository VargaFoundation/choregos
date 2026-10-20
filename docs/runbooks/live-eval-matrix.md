# Running the live eval matrix

*When:* measuring what rtk and headroom really save, before turning a reduction on by default
(ADR 0055, S37-10). It spends real model money: a full matrix is backends × models × 4 contexts ×
6 tickets real stages.

## Before

1. An **eval project**, used for nothing else:
   - a repository the platform's SCM app can write to, with a default branch holding at least one
     commit (a README is enough). The toy repositories are committed to
     `choregos-evals/<repo>-<digest>` branches of it, and each run pushes its work branch there;
   - the **internal** tracker (no tracker connector, or `internal`): eval work items never reach an
     external tracker, a cell with another tracker is skipped;
   - memory **off** in its policy; its `context` does not matter, the cell decides;
   - the backends and models to measure allowed by its policy and the gateway.
2. The deployment allows context reduction: `runner.contextReduction.enabled: true` (the default).
   With it off, every cell but `none` is skipped, and says why.
3. The runner image carries rtk and headroom (`docker/runner.Dockerfile`, built by `make images`; ADR 0055).

## Run

```bash
temporal workflow start --task-queue orchestrator --type EvalMatrix \
  --workflow-id evals-live-$(date +%Y%m%d) \
  --input '{"project_slug": "evals", "backends": ["claude-code"],
            "models": ["platform/standard"], "with_memory": [false],
            "contexts": ["none", "rtk", "headroom", "rtk+headroom"],
            "live": true, "publish": true}'
```

`with_memory: [false]` is required: a live cell with memory is skipped. `publish: true` writes the
cells to `GET /models/matrix` and the settings page.

## Read it

- `temporal workflow show --workflow-id evals-live-…`, or the settings page's matrix: per cell, the
  success rate, the medians of input, cache-read and output tokens, cost and turns, over the
  metered cases only.
- Each case is a work item `EVAL-<digest>` with label `eval:<ticket>` and one run: its page shows
  the context reduction card (what ran, what headroom says it removed, *inferred*) next to the
  gateway's cost (*observed*).
- The cell's cost is the gateway's. Check first that the gateway gives a price to cache reads and
  writes (`cache_read_input_token_cost`, `cache_creation_input_token_cost` in the model's
  `model_info`). Without them, a Claude Code run, which reads most of its input from the cache,
  costs mostly its output. That is the result the 2026-10-19 matrix got: ADR 0055 says how it was
  priced again from the spend logs. Both gateways price the cache since then (#737).
- A `skipped` case is not a failure and is not in the rate. Its reason says why: a tool missing on
  the runner, headroom unavailable, a backend without a shell-filter hook (rtk on anything but
  Claude Code), a stage that could not be prepared.

## After

Write the numbers and the per-backend decision in ADR 0055 (rule: a lower cost **and** a success
rate not lower, within the matrix's confidence interval). The eval work items stay, labelled
`eval`; the `choregos-evals/*` and work branches can be deleted from the eval repository.
