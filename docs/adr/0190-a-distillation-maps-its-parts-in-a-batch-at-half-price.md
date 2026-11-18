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
hours). The same holds for the evals, which are also non-interactive.

## Decision

1. **Scope: the map step of the distillation.** The reduce (a tree of dependent calls) stays synchronous;
   the evals are a follow-up (they run through the runner, not through a stateless call).
2. **An adapter, not the gateway.** LiteLLM is an external gateway and exposes no batch API we can rely
   on; `tranibor_adapters.batch` talks to the provider's Message Batches API directly (`BatchBackend`:
   `submit`, `poll`, `results`, `cancel`; `AnthropicBatches` over httpx; `FakeBatches` for ADR 0005). A
   request is keyed by `custom_id` (`part-<index>`); results come back in any order and are mapped by it,
   never by position (the fake answers in reverse to prove it).
3. **Opt-in, default off.** `knowledge_distill_batch` (with `_batch_model`, `_batch_api_key`,
   `_batch_base_url`, `_batch_poll_seconds`, `_batch_max_tokens`). Off because it needs a provider key that
   the gateway does not hold, an egress to the provider, a model name that must be the one the gateway
   alias resolves to, and a run that lasts up to a day instead of minutes. Without the key or the model,
   or when the provider refuses the batch, the run silently takes the synchronous path.
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
   (0.5), tokens as the provider reports them. This assumes `_batch_model` is the model the alias resolves
   to; a mismatch misprices, it does not fail. The plan's estimates are halved when batch mode applies, so
   the budget check compares like with like; a part stored by the batch is not checked again.
7. **Workflow.** Only a plan carrying `batch` takes the new branch (`submit_distillation_batch`, then
   `collect_distillation_batch` polled between `workflow.sleep` timers, 26 hours at most); a plan without
   it, which is every history recorded before, replays unchanged (a replayed history of each is in
   `tests/replay`, ADR 0008). Polling has its own retry policy; running out of it fails the run through
   `fail_distillation`.

## Consequences

- With the setting on and a provider key, a distillation costs half of its map bill and ends within a day.
- Not proven here: no real provider call was made. `AnthropicBatches` is proven against an httpx mock of
  the documented wire only.
- The ledger's cache columns (S4-07) and prompt-caching markers (S4-08) are not touched: a batched part
  books its input and output tokens like a synchronous one. Cache reads inside a batch are not priced until
  S4-07 lands.
- Follow-up: the evals (`activities/evals.py`) through the same adapter.
