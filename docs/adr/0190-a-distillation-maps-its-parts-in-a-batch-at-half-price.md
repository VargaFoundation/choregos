# 0190 — A distillation maps its parts in a batch, at half the price

- **Status**: accepted, 2026-11-18 (story S4-09)
- **Concerns**: `packages/adapters` (a new `batch` package and `fakes/batch.py`), `apps/api`
  (`knowledge/distill.py`, `config.py`), `apps/orchestrator` (`KnowledgeDistill`, two activities); builds on
  [ADR 0008](0008-idempotence-and-run-id.md), [ADR 0005](0005-adapters-and-fakes.md); depends on
  S4-07 (cache columns of the ledger) and S4-08 (prompt caching), which this ADR does not touch; stream S4

## Context

Nobody waits on a distillation: a knowledge base is distilled in the background and a person reviews the
result later. Its map step (one model call per part) is the largest model bill of the knowledge feature,
and the providers sell the same work at **-50 %** when it goes through their batch API (answers within 24
hours). The platform's models are reached only through the gateway (LiteLLM: ADR 0004 pricing, per-run keys,
egress), whose aliases map to OpenAI today (`platform/standard` is `openai/gpt-5.5`). The same holds for the evals, which are also non-interactive.

## Decision

1. **Scope: the map step of the distillation.** The reduce (a tree of dependent calls) stays synchronous;
   the evals are a follow-up (they run through the runner, not through a stateless call).
2. **An adapter over the gateway's batch API, never a provider directly.** LiteLLM (image v1.105.5)
   serves the OpenAI-compatible Files + Batches API, read in its source (`proxy/openai_files_endpoints/
   files_endpoints.py` `create_file`/`route_create_file`, `proxy/batches_endpoints/endpoints.py`
   `create_batch`/`retrieve_batch`, `router.py` `acreate_file`, `router_utils/batch_utils.py`
   `replace_model_in_jsonl`). `tranibor_adapters.batch.GatewayBatches` (`BatchBackend`: `submit`, `poll`,
   `results`, `cancel`; `FakeBatches` for ADR 0005) speaks it with the gateway URL and key the distiller
   already uses: `POST /v1/files` (`purpose=batch`, `target_model_names=<alias>`, JSONL rows
   `{custom_id, method, url: /v1/chat/completions, body}`), `POST /v1/batches`, `GET /v1/batches/{id}`,
   `GET /v1/files/{id}/content` (output and error files), `POST /v1/batches/{id}/cancel`. Naming the alias in
   `target_model_names` makes the router rewrite each row's `body.model` to the model the alias resolves to,
   so Choregos never needs the provider's model id (the former direct Anthropic adapter, its key, base URL
   and model setting are removed). The upstream decides: OpenAI-backed aliases batch natively; one without a
   Files/Batches API answers an error on upload or submit, which is the synchronous fallback. A request is
   keyed by `custom_id` (`part-<index>`); results come back in any order and are mapped by it, never by
   position (the fake answers in reverse to prove it).
3. **Opt-in, default off.** `knowledge_distill_batch` (with `_batch_poll_seconds`, `_batch_max_tokens`).
   Off because a run lasts up to a day instead of minutes, and because the gateway must run its managed-files
   hook (a database, which the `litellm-database` image has) for `target_model_names`. When the gateway or its
   upstream refuses the batch, or the key is unset, the run silently takes the synchronous path; a batch that
   ends `failed` yields no result and every part is distilled synchronously.
4. **One batch, submitted once.** `step_submit_batch` sends every part nobody has stored and keeps the
   batch id in the base's `distillation` record (`batch`), right after the provider answers. A retry, a
   restart or a replay finds it and polls; it never submits a second. No migration: the record is JSON.
   A crash between the provider's answer and that write can submit twice (the first batch then runs out
   unread); the cost is bounded to one extra batch, and a stopped or failed run cancels the batch it knows.
5. **Idempotent collection.** `step_collect_batch` is one poll. When the batch has ended it stores each
   answer by digest, as a synchronous part would, in the same transaction that books the cost
   (`record_cost(kind="distill")`); an answer already stored is never booked again. `step_part` then finds
   every stored part and calls the model only for one the batch could not answer (errored, expired,
   canceled, cut off, not JSON, or whose digest no longer matches): the synchronous path is the fallback.
6. **Price.** A batched part is booked at the catalogue price of the distiller's alias times `DISCOUNT`
   (0.5), tokens as the gateway reports them (`usage.prompt_tokens`, `completion_tokens`). The plan's estimates are halved when batch mode applies, so
   the budget check compares like with like; a part stored by the batch is not checked again.
7. **Workflow.** Only a plan carrying `batch` takes the new branch (`submit_distillation_batch`, then
   `collect_distillation_batch` polled between `workflow.sleep` timers, 26 hours at most); a plan without
   it, which is every history recorded before, replays unchanged (a replayed history of each is in
   `tests/replay`, ADR 0008). Polling has its own retry policy; running out of it fails the run through
   `fail_distillation`.

## Consequences

- With the setting on and a provider key, a distillation costs half of its map bill and ends within a day.
- Proven: `GatewayBatches` against an httpx mock of the wire read in LiteLLM's source, and the workflow
  against `FakeBatches`. Not proven: no live gateway or provider call was made (no money spent); whether the
  deployed LiteLLM has its managed-files hook enabled (`litellm_enterprise`, not in the open wheel, so not
  read), that the upstream accepts a `response_format` row in a batch, and the gateway's own accounting of a
  batch (LiteLLM prices a batch when a retrieve sees it completed; Choregos books its own ledger line at
  half price, so spend can show on both sides until reconciled).
- The ledger's cache columns (S4-07) and prompt-caching markers (S4-08) are not touched: a batched part
  books its input and output tokens like a synchronous one. Cache reads inside a batch are not priced until
  S4-07 lands.
- S4-10 finding (evals): no eval call in the orchestrator's evals can be batched. `activities/evals.py`
  plays a ticket or agent case as a runner stage (`seed_eval_ticket`, `prepare_stage`, `start_run`,
  `await_run`): a stateful, multi-turn agent run, which a batch cannot carry. Its verdicts (`_judge`,
  `tranibor_playbooks.evals.agents.judge`) are deterministic predicates over files, answers and journals:
  no model call. The stateless model calls that exist are elsewhere and all want a verdict now:
  the conversation judge (`tranibor_core.conversation_judge`, run by the API's `default_player` inside
  a promotion gate request, a verdict the promotion waits for), the playbook `--judge` and `--live-agents`
  of `python -m tranibor_playbooks.evals.runner` (a CLI that blocks CI). A batch ends within 24 h; none of
  these has a durable workflow to park on, so batching them would turn a gate into a day-long wait.
  Not forced: nothing was built. Reopen if a judge moves to a scheduled, non-blocking run (for example a
  nightly rubric sweep); it would then reuse `GatewayBatches` with the submit-once and timer pattern.
