# Plan — 2026-11-11: what is running, what it spends, and stopping it (stream S64)

Source: the product owner's request of 2026-11-11 (translated): "as in Claude Code's context view, see the
tokens consumed and their breakdown, for a workflow, an agent or assistant, or a project; see the tasks in
flight of a running workflow or of an agent or assistant, and be able to stop a task or an agent, for one
reason or another". Decisions taken with the product owner the same day: both the consumption and the context
composition; stopping an agent suspends it **and** stops what it is doing; one story per pull request.

Checked against `main` at `eb0c0524`. Stream number: **S64**. ADRs: **0141, 0142** (proposed).

Status words: **exists** (in the code), **missing**, **broken** (exists and is wrong).

## 0. What the audit found

| What is needed | Where | Today |
|:--|:--|:--|
| Tokens per line of spend | `cost_ledger.tokens_in/out/cached` | **exists** |
| Grouping by agent, workflow, project, model, stage | `routers/costs.py` `GROUPS` | **exists** |
| Narrowing a report to one agent, workflow or assistant | `_period`, the routes' query | **missing** (group only) |
| Grouping by run, by assistant installation | `GROUPS` | **missing**; `installation_id` is on the S63 branch only |
| Token totals | `CostTotal` | **missing** (money only) |
| Tokens on screen | `components/spend/breakdown.tsx` | **missing** (money only) |
| Spend of a run in flight | `book_spend` at run end; `Gateway.spend(key)` | **missing** on screen; the gateway can say |
| Context composition of a call | `prompt_loop`, `ModelProxy`, `llm_spans`, `agent_sessions.respond` | **missing**; `max_input_tokens` exists in the catalogue |
| What is in flight, across work items | `Run.status`, sessions, turns | **missing** (per work item only) |
| Stop a work item | `POST /work-items/{id}/actions` `stop` | **broken**: honoured between stages, the run in flight goes on |
| Stop one run | activity `cancel_run` | **missing** in the API (evals only) |
| Stop an agent | `Agent.status = suspended` refuses new runs | **missing**: its runs, sessions and turns in flight go on |
| Cancel a conversation turn | `POST /me/conversations/{id}/cancel` | **exists** (S63-04b branch), owner only |

## 1. Stories

Order of the pull requests: S64-00, S64-01, then S64-02, S64-03, S64-05 in parallel, then S64-04, S64-06,
S64-07, S64-08; S64-09 waits for S63-15 on `main`; S64-10 last.

### S64-00 · docs(adr): ADRs 0141 and 0142, this plan, the backlog stories

### S64-01 · contract(openapi): reports narrowed and in tokens, activity, cancel, stop, context

`CostReport` filters (`agent`, `workflow`, `assistant`, `work_item`) and groupings (`run`, `assistant`);
`CostTotal.tokens_in/out/cached`; `GET /orgs/{org}/activity`, `GET /orgs/{org}/activity/stream`;
`POST /runs/{id}/cancel`; `POST /orgs/{org}/agents/{slug}/stop`; `GET /runs/{id}/context`;
`ContextBreakdown`. **Accepted when** `make contracts` is regenerated and `contracts-check` is green.

### S64-02 · feat(api): a spend report narrowed to an agent, a workflow, a work item; grouped by run; in tokens (ADR 0141 §1)

**Accepted when** a report filtered by `agent=a` counts none of agent `b`'s lines, a `run` grouping names
each run by its work item key and stage, and the totals' tokens equal the sum of the lines'; each red with
the filter or the sum reverted. CSV follows.

### S64-03 · feat(api,orchestrator): a run stopped alone, and a work item's stop stops its run (ADR 0142 §2-3, §5-6)

`POST /runs/{id}/cancel` writes the `cancelled` result (first writer wins) and starts `cancel_run`;
`await_run` returns it; the interpreter, behind `workflow.patched`, takes a `cancelled` stage to a person
without retrying it. `stop` cancels the current run. **Accepted when** a fake run cancelled mid-stage ends
within one poll, the executor's `cancel` is called once, its spend is booked once, the work item waits for a
person, a run that has just finished keeps its result (409), and a recorded history replays
(`tests/replay/`).

### S64-04 · feat(api): stopping an agent suspends it and stops its runs, sessions and turns (ADR 0142 §4)

**Accepted when** after the stop the agent is `suspended` before any run is cancelled, every `queued` or
`running` run of the agent in every project is cancelled and no other agent's run is, its open sessions
are closed, the answer counts what was stopped, `agent.stop` is audited with the reason, and a caller
without `Action.MANAGE` gets 403. Depends on S64-03.

### S64-05 · feat(api): what is in flight, as one list and a stream (ADR 0142 §1, ADR 0141 §2)

`GET /orgs/{org}/activity` (runs, sessions; turns once S63 is on `main`) with filters, recently finished
entries, spend so far from the run's key (at most every 30 s per run, "—" when the gateway cannot say), and
the SSE stream built on the existing events. **Accepted when** a person who cannot read a project sees none
of its runs, the filters keep only their entries, and the stream sends `run.started` then `run.finished` for
a fake run.

### S64-06 · feat(web): an Activity screen and a stop with a reason

The organisation's and the project's Activity: in flight first, then finished, each entry expandable to its
steps (the run's events), elapsed time, tokens and cost so far; filters by workflow, agent, assistant; a Stop
button on a run, a work item and an agent, each asking a reason; the agent page's Stop says it suspends.
**Accepted when** the vitest tests show the list from the mocks, the stop sends the reason, and a refused stop
shows its message. Depends on S64-01; works against the fakes before S64-03/04/05 are merged.

### S64-07 · feat(web): tokens and their breakdown on a workflow's, an agent's and a project's page

A tokens-and-cost panel (input, output, cached; by model, by stage, by agent or workflow, by run) on the
workflow, agent and project pages and the Costs page, reading the report with its filter. **Accepted when**
each page passes its own filter and the totals read input, output and cache. Depends on S64-02.

### S64-08 · feat(runner,api): the context breakdown of prompt agents, hosted containers, sessions (ADR 0141 §3-6)

`ContextBreakdown` measured in `prompt_loop` (per call), in the loopback `ModelProxy` (hosted) and in
`agent_sessions.respond`; `context.breakdown` events throttled; `GET /runs/{id}/context`; the run page's
Context card (stacked bar, free space against `max_input_tokens`). **Accepted when** the sections of a scripted
call sum to the reported `prompt_tokens`, an MCP tool counts in `tools.mcp` and a runner tool in
`tools.platform`, no event carries text, and a model without `max_input_tokens` shows free space unknown.

### S64-08b · feat(gateway,runner): the context breakdown of ACP CLIs, from the gateway callback (ADR 0141 §4)

The runner registers the digests and sizes of what it writes (playbook, `task.md`, `CLAUDE.md`, skills, MCP
tool names); the LiteLLM callback counts each call's request and attributes the parts it recognises, the rest
to `backend`. **Accepted when** a recorded Claude Code request splits into the registered parts plus
`backend`, and a CLI request with nothing recognised is all `backend`, never an error.

### S64-09 · feat(api,web): an assistant's tokens and stopping an assistant (ADR 0141 §1, ADR 0142 §4)

Filter and grouping `assistant` on the ledger's `installation_id`; conversation turns in the activity list;
stopping an installation suspends it and cancels its turns in flight. Depends on S63-15 and S63-04b on
`main`.

### S64-10 · docs: the Activity and Costs pages of the user documentation

## 2. What this plan does not do

- No real tokenizer: chars/4, scaled to the exact total the model reports.
- No breakdown for the `code` and `remote` backends.
- No stop of a single tool call inside a run, no automatic restart after a resume.
