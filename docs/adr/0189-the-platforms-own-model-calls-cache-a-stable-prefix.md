# 0189 — The platform's own model calls cache a stable prefix

- **Status**: accepted, 2026-11-18 (story S4-08)
- **Concerns**: `packages/core/src/tranibor_core/prompt_cache.py` (new), `prompt_loop.py`; the direct
  gateway calls of `apps/api` (`knowledge/distill.py`, `services/agent_drafts.py`,
  `services/llm_routing.py`, `services/conversation_title.py`); stream S4. Companion of S4-07 (the ledger
  splits cache reads from cache writes); builds on [ADR 0084](0084-guardrails-outside-the-agent.md)
  only in that the gateway stays the single door.

## Context

The CLI agents Choregos launches (claude-code, codex, opencode, gemini-cli) manage their own cache. The
platform also calls models itself, always through the LiteLLM gateway on `/chat/completions` (there is no
`litellm` Python dependency: `uv.lock` has none; the gateway is the image `litellm-database` v1.105.5).
Those calls repeat a system prompt, instructions and a tool list, and `cache_control` appeared nowhere:
the repeated part was billed at full input price each time.

## Decision

1. **One function, `with_prompt_cache(body)`**, applied where a request body is built: `LoopRequest.body`
   (the prompt loop: assistant sessions, conversation evals, the runner's prompt stage) and the four
   one-shot calls above. It returns a copy; the caller's `messages` (extended in place by the loop) are
   never touched.
2. **Stable prefix, every provider.** Tools sorted by name; the leading `system` messages first; the
   volatile part (question, run ids, timestamps, per-item content) after. A `system` message met later in
   the history (a citation rule, an extras block) is volatile and is not marked. This is all OpenAI-style
   automatic caching needs (above 1024 tokens).
3. **Breakpoints, Anthropic family only.** `cache_control: {"type": "ephemeral"}` on the last tool and on
   the last block of the last leading `system` message (two breakpoints; Anthropic allows four, a marker
   already present is counted and the total is capped at four). Anthropic reads tools, then system, then
   messages, caches a prefix only above a per-model minimum (1024 to 4096 tokens: below it the marker is
   harmless) and keeps it 5 minutes, refreshed on each read.
4. **Family detection** is by name (`anthropic/`, `claude`, `anthropic.` as in `bedrock/anthropic.…`) plus
   `CHOREGOS_PROMPT_CACHE_MODELS`, a comma-separated list of gateway aliases (`platform/standard`) the
   deployment says are Anthropic: an alias does not name its provider, and the platform does not guess.
   Not listed: the body is sent as before, no field LiteLLM or another gateway may refuse.
5. **Verified against LiteLLM v1.105.5** (the wheel's source, not memory): `cache_control` is read from
   message content blocks, system blocks and tools in the Anthropic transformation, and removed for OpenAI
   (`remove_cache_control_flag_from_messages_and_tools`).
6. **Counters are read, not stored.** `cache_counters(usage)` reads `cache_read_input_tokens`,
   `cache_creation_input_tokens` and `prompt_tokens_details.cached_tokens`. Persisting them is S4-07's.

## Consequences

- The default alias of a deployment gains nothing until `CHOREGOS_PROMPT_CACHE_MODELS` names it (or the
  alias carries `claude`). A gateway-side alternative (`cache_control_injection_points` in the chart's model
  list, family-aware by construction) is left to a follow-up.
- Sorting the tools changes their order on the wire, not their meaning; `tool_choice` is by name.
- Not covered: the `/chat/completions` calls of `conversation_player`, `agent_sessions` and the runner take
  their body from the loop (covered); the realtime voice path and the eval judge bodies are not built here.
- A test fails if a timestamp enters the prefix (`packages/core/tests/test_prompt_cache.py`).
