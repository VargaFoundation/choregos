# 0141 — Tokens, and what fills the context, are read per run, workflow, agent, assistant and project

- **Status**: proposed, 2026-11-11
- **Concerns**: `cost_ledger` (`db/models/costs.py`), `routers/costs.py` (`GROUPS`, `_report`), `CostReport`,
  `CostTotal`, the spend screens (`components/spend/breakdown.tsx`), the runner's `prompt` backend
  (`runner/prompt.py`, `choregos_core/prompt_loop.py`), the hosted loopback `ModelProxy`
  (`runner/loopback/model.py`), the session loop (`services/agent_sessions.respond`), the LiteLLM callback
  (`choregos_core/llm_spans.py`), `GatewayModel.max_input_tokens`; builds on 0004 (cost counted at the
  gateway), 0018 (OTel GenAI), 0047 (a budget is a reservation), 0055 (fewer tokens per run), 0080 (hosted
  agents)

## Context

The ledger already books, per line, `tokens_in`, `tokens_out`, `tokens_cached` and the cost, with who spent
it: `project_id`, `work_item_id`, `run_id`, `agent_slug`, `workflow_name`, `session_id`, and on the
assistants' branch `installation_id` and `user_id`. `GET /projects/{id}/costs` and `GET /orgs/{org}/costs`
group it by `day`, `stage`, `model`, `backend`, `size`, `kind`, `agent`, `workflow`, `work_item`, `project`
or `origin`. Three things are missing:

- **Tokens are never shown.** The console draws money only. An operator cannot tell a run that read a lot
  from a run that wrote a lot, nor see what the cache saved.
- **A report cannot be narrowed to one agent, one workflow or one assistant.** It can be grouped by agent,
  but "what did agent X spend, by model and by stage" needs a filter, not a grouping. There is no
  grouping by `run` nor by assistant installation either.
- **Nobody knows what fills the context.** A run that costs 400 k input tokens may be a long history, a
  hundred MCP tool schemas, or a knowledge index pasted into the prompt. Claude Code's `/context` answers
  this for one session (system prompt, tools, skills, messages, free space); the platform has no
  equivalent, and ADR 0055's savings cannot be aimed without it.

What the platform can see of a request differs by backend (audit of 2026-11-11):

| Backend | Who builds the request | What the platform sees |
|:--|:--|:--|
| `prompt` (in-house loop), hosted sessions and conversations | the platform (`LoopRequest.body()`, `agent_sessions.respond`) | every section, before every call |
| `hosted` (container) | the container | the full request body, in the run's loopback `ModelProxy` |
| ACP CLIs (claude-code, codex, gemini-cli, goose, opencode, copilot-cli) | the third-party CLI | only what the platform supplies (playbook, `task.md`, `CLAUDE.md`, skill files, MCP server list); the gateway callback sees the whole request |
| `code` (agent SDK) | the author's code, in the agent's process | nothing trustworthy |
| `remote` (A2A) | another platform | nothing |

## Decision

1. **Consumption is one report, narrowed or grouped.** The cost routes keep their shape and gain:
   - filters `agent`, `workflow`, `assistant` (an installation id) and `work_item`, combinable with
     each other and with `kind`, `since` and `until`;
   - groupings `run` and `assistant` (named by the run's work item key and stage, and by the
     assistant's slug);
   - token totals in `CostTotal` (`tokens_in`, `tokens_out`, `tokens_cached`) next to the money.
   A workflow's, an agent's, an assistant's or a project's page reads the same report with its own filter.
   No new table: the ledger is the only source of what was spent (0004).
2. **What a run spent so far is read from its key, not booked.** A run's ledger line is written when the
   run ends (`book_spend`). While it is in flight, its spend is the gateway's count for its key
   (`Gateway.spend`), read at most every 30 seconds per run and never written to the ledger. A run whose
   gateway cannot say shows "—", never 0.
3. **The context breakdown is a measurement, made where the request is built.** Before each model call
   the platform builds, or sees, a `ContextBreakdown`: the call's `prompt_tokens` as the model reported
   it (the exact total), the model's `max_input_tokens` (free space = max − total, unknown when the
   catalogue says nothing), and sections:
   `system`, `tools.platform`, `tools.mcp`, `skills`, `knowledge`, `memory`, `messages`, and
   `backend` (what a third-party CLI adds that the platform did not write).
   Each section is estimated by the same counter (`estimate_tokens`, chars/4), then **scaled so the
   sections sum to the exact total**: the split is an estimate, the total is not, and the screen says so.
4. **Where it is measured, per backend**:
   - `prompt`, sessions and conversations: exact sections, from the request the platform sends.
   - `hosted`: from the body the loopback `ModelProxy` already parses; a message or tool it cannot
     attribute counts as `backend`.
   - ACP CLIs: the LiteLLM callback (`llm_spans`) already receives each call's messages and tools for
     a key that names a `run_id`. It counts the whole request and the parts the platform wrote (matched
     by the digests the runner registers at start: playbook, `task.md`, `CLAUDE.md`, skill files, MCP
     tool names). The rest is `backend`. A CLI version change can only move tokens into `backend`, never
     invent a section.
   - `code` and `remote`: no breakdown; the screen says "not measurable for this backend".
5. **What is kept is the numbers, never the prompt.** A breakdown is a run event (`context.breakdown`) or a
   session event, carrying the section sizes, the totals, the model and the call's ordinal: no text, no
   tool schema, no digest of the person's input. It inherits the retention of the run's events (and of the
   conversation's, ADR 0134). At most one event per call, and at most one per 5 seconds per run: a run of
   300 calls keeps its first, its last and the largest of each window.
6. **Read where the rest is read.** `GET /runs/{id}/context` returns the last breakdown and the series of
   totals; a conversation's own route returns it to its owner only. The breakdown is read with the run's
   (or the conversation's) read right, nothing wider.

## Consequences

- A workflow, an agent, an assistant and a project each get a tokens-and-cost panel with the same
  groupings; an operator sees input, output and cache, not only money.
- An operator sees, for an in-flight or finished run, what fills its context and how close it is to the
  model's limit: the lever ADR 0055 needed.
- The callback does a little more work per call (counting a request it already holds); the run's event
  stream gets at most one more event per 5 seconds.
- Not covered: a real tokenizer per model (chars/4 scaled to the exact total is enough to rank sections);
  a breakdown for `code` and `remote`; attributing a CLI's own system prompt and built-in tools to anything
  finer than `backend`; a budget that reads the breakdown.
