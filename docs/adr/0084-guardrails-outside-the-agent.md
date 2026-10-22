# 0084 — Guardrails sit outside the agent, at six points; the LLM call's are at the gateway

- **Status**: accepted, 2026-10-22 — the request-side LLM guard is implemented by S40-21 (secret
  masking, §3); the response-side guard and size or PII limits are not (§5)
- **Concerns**: the LiteLLM chart (`charts/choregos/charts/litellm`), the local install
  (`choregos_api.local_demo`), the run key (`activities/run_key.py`) and the hosted session key
  (`services/agent_sessions.py`), `choregos_core.llm_guardrail`, the policy flag
  `sandbox.llm_security_analyzer`; builds on [ADR 0004](0004-cost-counted-at-the-gateway.md) (the gateway counts
  and caps), [ADR 0010](0010-gates-are-mechanisms.md) (gates), [ADR 0055](0055-fewer-tokens-per-run.md)
  (headroom in the runner pod) and [ADR 0070](0070-network-access-is-bounded-and-enforced-per-run.md)
  (network per run)

## Context

A guardrail checks what goes into or comes out of a model or a tool. Choregos had several, built
story by story; nobody had listed them, and one point had none: the LLM call itself. Nothing read a
request on its way to the provider, so a key the agent read from a file, or a bearer token in a log
it was shown, left for the provider in the next prompt.

The policy declared `sandbox.llm_security_analyzer` (true in the `team` and `regulated` presets)
and nothing enforced it: a leftover of the OpenHands executor removed by [ADR 0011](0011-openhands-removed.md).

## Decision

1. **A guard never runs in the agent's process.** The agent is the thing being guarded: whatever it
   can reach, it can switch off. Every guard runs in the platform (API, orchestrator), in the runner
   process the agent cannot rewrite, or at the gateway.
2. **The six points, and where each is enforced.**

   | Point | What is checked | Where |
   |---|---|---|
   | Before the agent's input | prompt injection in the ticket, documents, memory (`sandbox.prompt_injection`) | orchestrator, `activities/garde.py` |
   | Before an LLM call | secrets masked in the request (`sandbox.llm_security_analyzer`, this ADR) | gateway, LiteLLM `pre_call` guardrail |
   | After an LLM call | nothing yet (§5) | — |
   | Before a tool call | ACP permissions, `sandbox.deny_commands`, network policy; operation policies | runner (`choregos_runner/guardrails.py`); API broker |
   | After a tool call | bounds and masking of results (S40-13, S40-15); injection guard on fetched and broker answers | API (`services/courtier.py`, `routers/internal.py`) |
   | After the agent's output | Definition of Done, diff checks, gates | orchestrator (ADR 0010) |

3. **The LLM guard is a LiteLLM custom guardrail, marked per key.** `choregos_core.llm_guardrail`
   is stdlib only; the chart ships a byte-identical copy beside LiteLLM's `config.yaml` (a test
   refuses any divergence) and the local install writes it next to its config. It is configured
   `mode: pre_call, default_on: true` and acts only for a key whose **metadata** carries
   `choregos_llm_guard: redact`. The platform writes that entry when it mints a run's key (start and
   resume share `mint_key`) or a hosted session's key and the effective policy sets
   `sandbox.llm_security_analyzer`. A key's metadata is written by the platform with the master key;
   the agent holds its key but cannot change it, and the guard never reads the request's own
   `metadata`.
4. **What it masks: high precision only.** The model sees what is left and may write it back, so the
   guard masks the `no_secrets` gate's token shapes (kept equal to `SECRET_PATTERNS` by a test), a
   bearer or basic credential of 16 characters or more holding a digit, and a URL's literal
   user-info; never `key = value` assignments (`password = getpass()` is code). It walks the text
   fields of the OpenAI and Anthropic shapes (`messages`, `system`, `input`, `prompt`,
   `instructions`) and leaves media blocks alone. An error in the guard is LiteLLM's error for the
   request, which then never reaches the provider: it fails closed.
5. **Not decided here.** A response-side guard (`post_call`: output policy, secrets the model
   produced) and size or PII limits on the request. They go in this guardrail when a story needs
   them, marked the same way.

## Consequences

- `llm_security_analyzer` means something: in the `team` and `regulated` presets the provider no
  longer receives the secret shapes above. An edit whose old text held a masked value fails, because
  the model saw `[REDACTED]`; that is the price of the value not leaving.
- Only the LiteLLM gateway enforces it. The `direct` gateway has no proxy and the
  `openai-compatible` gateway is someone else's: with either, the flag marks a key that nothing
  reads. The run's access record does not say so yet.
- The guard is a filter, not a scanner: it does not replace gitleaks, the `no_secrets` gate or a
  provider's zero-retention terms.
- LiteLLM's custom guardrail interface is the integration point; an upgrade of the gateway image
  must keep `async_pre_call_hook` and file-relative loading, which only a live gateway proves.

## Alternatives rejected

- **Masking in the runner (headroom, ADR 0055).** Headroom is optional and lives in the agent's pod;
  a run without it, or a backend that bypasses it, would send the secret.
- **Masking in the agent CLI's hooks.** The agent's process, which point 1 excludes.
- **Turning the guard on by request metadata.** The agent writes the request.
