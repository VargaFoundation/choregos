# 0190 — A distillation maps its parts in a batch, at half the price

- **Status**: accepted, 2026-11-18 (story S4-09; amended by S4-11, see the end)
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
   already uses: `POST /v1/files` (`purpose=batch`, `target_model_names=<alias>` (amended, S4-11: now `x-litellm-model: <alias>`), JSONL rows
   `{custom_id, method, url: /v1/chat/completions, body}`), `POST /v1/batches`, `GET /v1/batches/{id}`,
   `GET /v1/files/{id}/content` (output and error files), `POST /v1/batches/{id}/cancel`. Naming the alias in
   `target_model_names` makes the router rewrite each row's `body.model` to the model the alias resolves to,
   so Choregos never needs the provider's model id (the former direct Anthropic adapter, its key, base URL
   and model setting are removed). The upstream decides: OpenAI-backed aliases batch natively; one without a
   Files/Batches API answers an error on upload or submit, which is the synchronous fallback. A request is
   keyed by `custom_id` (`part-<index>`); results come back in any order and are mapped by it, never by
   position (the fake answers in reverse to prove it).
3. **Opt-in, default off.** `knowledge_distill_batch` (with `_batch_poll_seconds`, `_batch_max_tokens`).
   Off because a run lasts up to a day instead of minutes. (Amended, S4-11: no managed-files hook is needed.) When the gateway or its
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

## Amendment (S4-11): the open path, and the cost of record

The first version sent `target_model_names` on upload. In LiteLLM that goes through the `managed_files` hook, which
lives in `litellm_enterprise` under the BerriAI Enterprise licence. Choregos must not need it. Read in the extracted
v1.105.5 source, the open path is:

- **Upload** (`proxy/openai_files_endpoints/files_endpoints.py` `create_file` / `route_create_file`): the model is
  taken from the form, `?model=` or the `x-litellm-model` header (`common_utils._extract_model_param`).
  `get_authorized_credentials_for_model` calls `authorize_model_for_key` (the key's model grants apply), then
  `router.get_deployment_credentials_with_provider` (`router.py`) returns the deployment's credentials and
  `litellm_params.model`. `litellm.acreate_file` uploads with them, and the id returned is
  `encode_file_id_with_model` (`file-<base64 "litellm:<id>;model,<alias>">`). Nothing rewrites the rows on this
  path (the rewrite, `_litellm_executed_batch_input_model`, belongs to the managed path), so each row's
  `body.model` must already be the upstream id.
- **Batch ops** (`proxy/batches_endpoints/endpoints.py` `create_batch` "SCENARIO 1", `retrieve_batch`, cancel,
  and `get_file_content` via `common_utils.decode_model_from_file_id` / `get_credentials_for_model`): the alias is
  decoded from the ids, so `POST /v1/batches` and the others need no model header; the batch, output and error
  ids come back encoded with the alias and are sent back untouched.
- **Alias to upstream model**: `GET /v1/model/info` (`proxy_server.py` `model_info_v1`, `user_api_key_auth`) lists
  what the key may use, `litellm_params.model` included (credentials masked). `GatewayBatches.upstream_model`
  reads it once per alias per adapter and takes `openai/<id>` to `<id>`; none, several or a non-OpenAI deployment
  is a `BatchError` before any upload, which is the synchronous fallback.
- **Do not set** `litellm_settings.require_managed_files` (`common_utils.validate_managed_files_requirement`): it
  makes every `model=` upload answer 400.

**Cost of record.** The Choregos ledger line (`record_cost(kind="distill")`, half the catalogue price, decision 6)
is the source of truth for a distillation. It is booked on the project or the organisation with `run_id` and
`work_item_id` both empty, so no run or work-item budget sees it. A run's spend is read from the gateway for the
run's own key (`collect_spend` / `book_spend` call `gateway.spend(run.gateway_key_id)`); the distiller uses the
gateway's master key, which nothing reads. LiteLLM does price a retrieved completed batch
(`litellm_logging.py` async success on `aretrieve_batch`, `batches/batch_utils.py` `batch_cost_is_final` /
`_handle_completed_batch`) as a spend log under the calling key; that log is informational, the same as for the
synchronous calls, and is never added to the ledger. Pinned by
`apps/api/tests/test_knowledge_distillation_batch.py::test_a_batched_part_is_booked_once_and_on_no_run_or_work_item_budget`.

Still not proven: no live gateway or provider call; the open path is read in source only.
