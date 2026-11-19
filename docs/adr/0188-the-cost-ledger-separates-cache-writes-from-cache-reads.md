# 0188 — The cost ledger separates cache writes from cache reads

- **Status**: accepted, 2026-11-18 (S4-07)
- **Concerns**: `Spend` (`tranibor_core.domain`), the LiteLLM adapter, `cost_ledger`, the cost report
  (`GET .../costs`), the run's `tokens` and the work item's `totals`; relates to
  [ADR 0141](0141-tokens-and-the-context-are-read-per-scope.md) §1 and
  [ADR 0098](0098-english-everywhere-finished.md)

## Context

On real agent workloads cache reads and writes are 85 to 98 % of the cost. An Anthropic cache write
costs 1.25 times (5-minute TTL) or 2 times (1-hour TTL) the input price, a read 0.05 to 0.1 times. The
ledger held one `tokens_cached`, and that was only the **reads**: the gateway adapter read
`cache_read_input_tokens` (Anthropic) or `prompt_tokens_details.cached_tokens` (OpenAI) from the spend
logs and never `cache_creation_input_tokens`, which LiteLLM does put in the same usage object. The
writes were counted in `tokens_in` and nowhere else, so nobody could see where an agent's money goes.
The cost in dollars itself was right: it is LiteLLM's `spend`, priced by the provider's own multipliers.

## Decision

1. **Add `tokens_cache_write`; keep `tokens_cached` as the cache reads.** A `Spend`, a `cost_ledger`
   line, a report row and total, a run's `tokens` and a work item's `totals` carry both. Both are part of
   `tokens_in`. A write is read from `cache_creation_input_tokens` (top of the spend-log entry, else
   `metadata.usage_object`); OpenAI reports none, so its writes are 0.
2. **No rename, no alias.** The name `tokens_cached` already means "read from the cache" in the contract
   (`openapi.yaml`: "the input tokens the model read from its cache") and in ~80 files, the web, the CLI,
   the VS Code fake and the eval matrix. Renaming it to `tokens_cache_read` would be a breaking contract
   change for every consumer, and ADR 0098 forbids keeping the old name as an alias. The change is
   additive (default 0) and is the contract PR that precedes the implementation (ADR 0001). The
   asymmetry (`tokens_cached` / `tokens_cache_write`) is the price; documented on every field.
3. **Migration**: `cost_ledger.tokens_cache_write`, `NOT NULL DEFAULT 0`. A line booked before it reads 0,
   which means "not told", not "none"; nothing is backfilled (the gateway's logs of a revoked key are not
   a reliable source).
4. **The fake gateway prices a write at 1.25 times** (the 5-minute TTL), next to its read at 0.1 times,
   so a test can see the difference.
5. **Pricing stays the gateway's.** The platform does not compute a price from tokens, except for an
   organisation's `input_per_1k_override` (`priced_spend`) and the realtime relay. Both are left as they
   were here: the override prices every input token at one rate, cache included, which over- or
   under-states a cache-heavy run. It is recorded as a finding, not fixed in this story, because a
   correct rule needs cache multipliers the override does not carry (and the TTL a write used, which the
   spend log does not say).

## Not done

- The CSV export keeps its columns (a consumer parses them by position).
- The activity feed, the eval matrix and the web/CLI panels still show reads only.
- The realtime relay's `Spend` reports no writes (the Realtime API has none).
