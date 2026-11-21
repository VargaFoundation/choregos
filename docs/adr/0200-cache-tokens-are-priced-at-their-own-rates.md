# 0200 — Cache tokens are priced at their own rates

- **Status**: accepted, 2026-11-21 (S4-12)
- **Concerns**: `Spend`, the LiteLLM adapter, `cost_ledger`, the model catalogue, `priced_spend`, the
  cost report; completes [ADR 0188](0188-the-cost-ledger-separates-cache-writes-from-cache-reads.md) §5;
  findings #1893 and #1895

## Context

ADR 0188 left two findings. (1) `priced_spend` with an organisation's `input_per_1k_override` multiplied
every input token by one rate, so a cache read (0.05 to 0.1x on Anthropic) and a cache write (1.25x for
the 5-minute TTL, 2x for the 1-hour one) were priced at 1x. (2) The ledger had one write counter, so a
1-hour write (2x) was told from a 5-minute one (1.25x) nowhere: booked as one, a 1-hour write is
under-priced by 37 %. Without an override the gateway's own `spend` is booked and is right.

## Decision

1. **The ledger gets a second column, `tokens_cache_write_1h`, not a TTL dimension.** It is a *part* of
   `tokens_cache_write` (the 5-minute writes are the difference). A TTL dimension would multiply the
   ledger's rows (one per run segment today) and break every sum the report, a run's `tokens` and a
   work item's `totals` do over a line; a part-of column keeps `tokens_cache_write` meaning what ADR
   0188 said, adds up in the same `SUM`, and a line booked before it (0) reads as "5-minute or not
   told", which is the provider's default. Only two TTLs exist; a third would be a new column, which
   is cheaper than a dimension for a counter this small.
2. **The split is read where the gateway puts it.** LiteLLM v1.105.5 keeps Anthropic's `cache_creation`
   under `prompt_tokens_details.cache_creation_token_details.{ephemeral_5m,ephemeral_1h}_input_tokens`
   in the request's usage (`metadata.usage_object`, copied into `metadata.additional_usage_values`;
   `proxy/db/autorouter_session_rollup.py` reads the same path); a pass-through call keeps Anthropic's
   own `cache_creation` object. The adapter reads those, clamps the 1-hour part to the entry's writes,
   and counts a write with no split as 5-minute. Providers that report none (OpenAI) stay 0.
3. **Multipliers belong to the catalogue entry**: three nullable columns on `model_catalogue`
   (`cache_read_multiplier`, `cache_write_5m_multiplier`, `cache_write_1h_multiplier`), set through
   `cache_multipliers` on the entry's create and patch bodies (`clear_cache_multipliers` resets). The
   entry reads back its own values and `effective_cache_multipliers` (what applies, and where the read
   multiplier comes from: `override`, `gateway`, `family_default`).
4. **Resolution**: the entry's own value; for the read only, the ratio of the gateway's
   `cached_input_cost_per_1k` to its input price when both are known; else the default of the model's
   provider family (`tranibor_core.cache_pricing`): Anthropic 0.1 / 1.25 / 2 (read 0.05 on Claude Opus
   5.5 and Sonnet 5.5), OpenAI and Azure 0.5 / 1 / 1, Gemini 0.25 / 1 / 1, anything else 1 / 1 / 1
   (what the override did before). Sources: the providers' published prompt-caching list prices.
   Where a family's ratio varies by model (OpenAI 0.1 to 0.5, Gemini 0.1 to 0.25) the default is the
   dearer one, so a default never under-states a cost. The Opus 5.5 / Sonnet 5.5 figure is taken from
   the story and was not re-checked against a live price list.
5. **`priced_spend`** prices fresh, read, 5-minute and 1-hour input tokens each at `input_per_1k x`
   their multiplier (counts clamped to their whole), plus output as before, and records the multipliers
   used in the run's `tokens.cache_multipliers`. The gateway's figure is still the booked one without
   an override.

## Not done

- No backfill: an older line has 0 one-hour writes and, before ADR 0188, 0 writes.
- The gateway's `cache_creation_input_token_cost` (LiteLLM model info) is not read to derive the write
  multipliers; the entry or the family default decides.
- The fake gateway prices a 1-hour write at 2x; no real provider call was made.
- The realtime relay and the CSV export are unchanged; web and CLI do not show the 1-hour split yet.
