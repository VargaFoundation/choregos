# Plan — 2026-11-03: an agent calls its run's tools from a script (tool scripts)

Source: the product owner's question of 2026-11-03: "instead of 36 round trips between the MCP servers and the
model, could the agent write a piece of code, run it all, and let the model analyse the final answer? Analyse it
and say whether Choregos should have it", then "prepare the whole stream: the plan, the ADR, the success
criteria, the metrics, the examples to integrate". The idea is Anthropic's engineering note "Code execution with
MCP" (2025-11-04): present the tools as a code API, let the model write one script that chains the calls, and
let it read only what the script prints.

Checked against `main` at `e3c8f58d`. Stream number: **S57**. ADR number reserved: **0122**
([the decision](../adr/0122-an-agent-may-call-its-run-s-tools-from-a-script.md)). The scenarios to integrate are
in [PLAN-2026-11-03-tool-scripts-examples.md](PLAN-2026-11-03-tool-scripts-examples.md).

Status words: **exists** (in the code), **missing**, **broken** (exists and is wrong).

---

## 0. What the audit found before planning

| Question | What the code says | Verdict |
|:--|:--|:--|
| Can the agent run code? | Every shell backend runs Bash and Python in the run's sandbox, confined by gVisor or Landlock (ADR 0094) and the egress proxy (ADR 0070) | Exists — no new runtime needed |
| How does a platform tool reach the agent? | The runner's local MCP server (`localhost:7777/mcp`, `outils_locaux.py`, `choregos_tools_mcp/app.py`) relays `tools/call` to `POST /internal/runs/{id}/tools/{name}` with the runner's run token | Exists |
| Is the local tool server a boundary? | No authentication; any process of the sandbox can post `tools/call`; `NO_PROXY` and the confinement's ports already allow it. Such a call skips the runner's permission check, turn counter and journal | Exists, and it is not a boundary — ADR 0122 §2 says so |
| Does the API govern each call? | Allowlist, cap (`budgets.tool_calls_per_run`), approvals → proposals (ADR 0035), governed writes with `call_id = uuid5(run, tool, args)`, bounds and masking, injection guard, ledger row, `choregos.tool.called`, `execute_tool` span (`routers/internal.py:917-1096`) | Exists — the guarantee code mode rests on |
| Does the cap hold under concurrency? | Count of ledger rows, then the row is inserted later, without a lock (`internal.py:944-958`) | **Broken** under concurrent calls |
| Does the model learn that a write was only proposed? | The MCP server returns only `result` (≤ 20,000 chars), `isError` only from 400: a `202` proposal reads as a success; `remaining` and `suspicions` are dropped (`tools-mcp/server.py:77-89`) | **Broken** |
| Does the agent hold the run token? | `agent_sandbox`: no (in memory only). `k8s_job`, ACA, local Docker: the container's env has `CHOREGOS_RUN_TOKEN` (`executor/k8s_job.py:283-286`) and the agent inherits `os.environ` (`acp/client.py:117`, `runner.py:583`) | **Broken** on three executors (contradicts ADR 0115) |
| Can a script read a tool's access or output schema? | `getRunTools` gives `name`, `description`, `inputSchema`; read/write/approval only as prose in the description; `ConnectorOperation.output_schema` is stored, never announced | Missing |
| Are round trips measured? | `run.tokens` has `tokens_in/cached/out`, cost; `Spend.requests` (model calls) is computed (`core/domain.py:363`) and not persisted; `choregos.tool.called` has no size and no origin; diagnostics have `turns` and `tool_calls` | Partial |
| Can the eval matrix compare two ways of calling tools? | `EvalMatrix` has a `context` dimension (S37-09) to copy; scoring has medians of cost, tokens, turns, success rate; **no confidence interval in code** (ADR 0055's bootstrap was computed by hand); agent cases (`agent_cases/*.yaml`) script tool evidence on fakes | Reusable, the interval is missing |
| Is there a production baseline? | Not taken: reading production from an agent session was refused on 2026-11-03; the query is in §4.3 for the operator | Missing — story 01 |

Conclusion: the platform already governs every call where it matters (the API), and the agent already runs
code. What is missing is an **official door** for scripts that keeps the API's whole answer, a **client** the
model can read cheaply, **attribution** of calls to scripts, three **fixes** that hold for both doors, and the
**measurement** that decides, agent by agent, whether it pays.

## 1. Decisions

The decision is [ADR 0122](../adr/0122-an-agent-may-call-its-run-s-tools-from-a-script.md). In one paragraph: a
second route on the runner's local tool server (`/bridge/v1/tools/{name}`) relays a script's call to the same
`callRunTool` and returns its answer unchanged; a generated, standard-library client (`choregos_tools`) types
the outcomes (result, `Proposal`, `CapReached`, `Withheld`, `ToolError`); governance does not depend on the
door and no rule is keyed on it; the door is recorded as attribution (`origin`, script digest and text); the
model keeps its MCP tools and chooses (hybrid); off by default behind three switches; default-on per agent only
when the matrix shows a lower cost per successful run and a success rate that is not lower.

The shape of the policy, for reference (contract in story 05):

```yaml
tools:
  scripts: offer              # off (default) | offer
  max_concurrent_calls: 4     # calls of one run the bridge relays at once
  per_role:
    review: { scripts: off }
```

What a script looks like to the model (the client of story 08):

```python
from choregos_tools import pfsense, Proposal

rules = pfsense.list_firewall_rules()
aliases = {a["name"]: a for a in pfsense.list_aliases()["aliases"]}
open_ssh = [r for r in rules["rules"] if r["destination_port"] == "22" and r["source"] in ("any", "0.0.0.0/0")]
for rule in open_ssh:
    print(rule["id"], rule["interface"], rule["descr"])
print(f"{len(open_ssh)} of {len(rules['rules'])} rules open SSH to any source")
```

## 2. Stories and order

Waves are the backlog's `sprint`: a story only depends on stories of an earlier wave.

| Story | Wave | Title | Depends on |
|:--|:-:|:--|:--|
| S57-00 | 1 | This plan, ADR 0122 (proposed), backlog and STATUS rows | — |
| S57-01 | 2 | Measure round trips: persist model requests per run, `response_bytes` on `choregos.tool.called`, a report; the operator runs it on production | 00 |
| S57-02 | 2 | The agent never holds the run token, on any executor | 00 |
| S57-03 | 2 | The tool-call cap holds under concurrent calls | 00 |
| S57-04 | 2 | The MCP route keeps the answer's meaning: proposal, remaining, suspicions | 00 |
| S57-05 | 3 | contract-change: `Policy.tools`, the ceiling, `StageInput.tools.scripts`, tool annotations, origin headers and event fields | 01 |
| S57-06 | 4 | API: origin and script digest on the ledger, the event and the span; costs grouped by origin | 03, 05 |
| S57-07 | 4 | Runner: the tool bridge on the local tool server, journaled, bounded | 02, 04, 05 |
| S57-08 | 5 | Runner: the generated client `choregos_tools`, its index and the instructions; shell backends wired | 07 |
| S57-09 | 6 | Evals: a `tools` dimension, connector scenarios, new assertions, a paired bootstrap interval in the matrix | 01, 08 |
| S57-10 | 5 | Console: a run's calls grouped by script, the script's text; spend by origin; the policy switch | 06, 07 |
| S57-11 | 7 | Live matrix on dev, per-agent default, STATUS and ADR 0122 accepted or amended | 08, 09, 10 |
| S57-12 | 8 | Phase 2 — on-demand disclosure (`tools.disclosure`), measured | 11 |
| S57-13 | 8 | Phase 2 — a script that worked becomes a proposed skill (ADR 0050 gate) | 11 |
| S57-14 | 8 | Phase 2 — hosted runtimes reach the bridge through the loopback MCP proxy (ADR 0080) | 11 |

```
wave 1  S57-00
wave 2  01 measure · 02 run token · 03 cap lock · 04 MCP answer        (all after 00)
wave 3  05 contracts                                                  (after 01)
wave 4  06 attribution (after 03, 05) · 07 bridge (after 02, 04, 05)
wave 5  08 client (after 07) · 10 console (after 06, 07)
wave 6  09 evals (after 01, 08)
wave 7  11 live matrix and decision (after 08, 09, 10)
wave 8  12 disclosure · 13 skills · 14 hosted runtimes                (after 11, phase 2)
```

Phase 2 (12-14) starts only if story 11 meets the gate of §5.2; otherwise the stream stops at 11 with its
numbers recorded, and the feature stays `off`.

### S57-00 · docs(adr): an agent may call its run's tools from a script (ADR 0122)
This plan, its examples, ADR 0122 (`proposed`), the backlog's S57 stories, the STATUS row. **Proves**: the ADR
index and the STATUS total tests. **Does not prove**: anything about the code.

### S57-01 · feat(orchestrator): round trips are measured on every run
- `book_spend` persists the gateway's `Spend.requests` as `run.tokens.requests` (the model calls of the run;
  `Totals` gains `requests`, contract-change in the same PR, null for runs before it).
- `choregos.tool.called` gains `response_bytes` (the canonical JSON size of the answer the API returns).
- `tools/report_round_trips.py`: per agent and backend over a window, the runs, success rate, medians and p90 of
  model requests, platform tool calls, agent tool calls, `tokens_in`, `tokens_cached`, `tokens_out`, cost and wall
  time, and the share of runs with ≥ 5 platform tool calls. Reads through the public API with an operator token
  (no route lists runs over a window: projects → work items → runs → `/runs/{id}/access`); §4.3 gives the same as
  SQL (`--sql`).
- The operator runs it on production (30 days) and the numbers are written into §4.4 of this plan.
- **Test that fails without it**: a run booked from a fake gateway with 7 requests reads `tokens.requests == 7`;
  an event carries `response_bytes` equal to the answer's size.

### S57-02 · fix(runner): the agent never holds the run token, on any executor
- The runner reads `CHOREGOS_RUN_TOKEN` and `CHOREGOS_RUN_REMOTE_TOKEN` into its settings, then builds the agent's
  environment without them (and without any `CHOREGOS_RUN_*` secret), on every path: ACP backends, the `code`
  backend, the confinement wrapper, hosted containers.
- **Test that fails without it**: for `k8s_job`, ACA, local Docker and Tekton environments, the agent's launched
  environment (as `AcpClient` receives it) holds no run token; the job step still receives it (ADR 0056).
- Amends nothing: it makes ADR 0115's sentence true on every executor.

### S57-03 · fix(api): the tool-call cap holds under concurrent calls
- `callRunTool` takes a per-run transaction lock (`pg_advisory_xact_lock` on the run id, a no-op on SQLite)
  before it counts the run's `kind="tool"` rows, and inserts its row in the same transaction.
- **Test that fails without it**: at `cap - 1`, eight concurrent calls let exactly one through and seven get the
  429 problem (PostgreSQL test; the SQLite suite checks the lock is asked for).

### S57-04 · fix(tools-mcp): the MCP route keeps the answer's meaning
- The tool server's `tools/call` answer keeps `status_code`; a `202` with an `action` is rendered as "proposed,
  not executed: a person decides at <decision_path>" (`isError: false`, but the text says it); `remaining` is
  appended when the cap is set; `suspicions` are rendered before the result ("the platform flagged this result as
  a suspected injection: …").
- **Test that fails without it**: a proposal, a flagged result and a near-cap call, each rendered as above.

### S57-05 · contract(schemas): tool scripts in the policy, the stage input and the tool announcements
- `policy.schema.json` and `Policy`: a top-level `tools` (`scripts: off | offer`, `max_concurrent_calls` 1-16,
  `per_role`); the ceiling's `tools.scripts` may only forbid. Presets unchanged (`off`).
- `stage-input.schema.json` and `StageInput.tools.scripts` (`{max_concurrent_calls}`), left out of the payload
  when off (`context_reduction.stage_input_payload`'s rule).
- Orchestrator: `resolve_tool_scripts(policy, role, allowed_by_deployment)` beside `resolve_context`; the kill
  switch `CHOREGOS_TOOL_SCRIPTS` and its Helm value.
- OpenAPI: `getRunTools` entries gain MCP's `annotations.readOnlyHint` and, beside it at the entry's top level,
  `x-choregos-access: read|write|approval`, and `outputSchema` when the operation declares one (the run's
  tool server lists neither `outputSchema` nor `x-choregos-*` to the model); `callRunTool` accepts
  `X-Choregos-Tool-Origin` (`model|script|job`) and `X-Choregos-Script` (64 hex).
- `choregos.tool.called` gains `origin` and `script_sha256` (the event schema's `data` is open: the fields
  are documented in `docs/usage.md`).
- **Tests that fail without it**: schema and model declare the same fields; the ceiling refuses `offer` under
  `off`; an inactive setting is absent from the payload; a run of `review` with `per_role` off gets none.

### S57-06 · feat(api): calls are attributed to their door
- The origin and script digest land in the ledger row's `dimensions`, `choregos.tool.called` and the span
  attribute `choregos.tool.origin`; an absent header is `model`, a job's token is `job`, any other value is
  refused (400).
- `/projects/{id}/costs` and `/orgs/{org}/costs` group by `origin`.
- **Tests that fail without it**: one call per origin, each recorded with its origin; grouping returns the three.
  The origin is never read by an authorisation path (a test asserts that a refused call is refused for every
  origin).

### S57-07 · feat(runner): the tool bridge
- `GET /bridge/v1/tools`, `POST /bridge/v1/tools/{name}` on the local tool server (the runner's and the Tekton
  sidecar's — the same app), mounted only when `StageInput.tools.scripts` is set; relays with origin `script`,
  forwards `X-Choregos-Script`; at most `max_concurrent_calls` in flight; 60 s per call (a governed write waits up
  to 30 s).
- `POST /bridge/v1/scripts` takes a script's text once (≤ 64 KiB) and journals `tools.script`; each call is
  journaled `tools.script.call`. No new port: the confinement's port set is unchanged (a test says so).
- A fake API for the runner's tests; `CHOREGOS_FAKES=1` serves it in `make demo`.
- **Tests that fail without it**: a call relayed with the run token and the origin header, the API's answer
  returned byte for byte; the ninth concurrent call waits; the route is absent when scripts are off; the run token
  never appears in a response or in the journal.

### S57-08 · feat(runner): the generated client and its instructions
- Per run, from `GET /bridge/v1/tools`: `$TMPDIR/choregos/tools/choregos_tools/{__init__,<server>}.py` and
  `INDEX.md`; standard library only (Python ≥ 3.9); `CHOREGOS_TOOLS_DIR` and `PYTHONPATH` set in the agent's
  environment; the directory git-excluded and outside the workspace.
- Outcomes: result, `Proposal`, `CapReached`, `Withheld`, `ToolError`; `suspicions` on standard error; the first
  call posts the running script's text; `search(query, detail)` over the index.
- Instructions (≤ 25 lines, added to `task.md` only when offered): when a script pays, print only what the next
  decision needs, never print a secret, write scripts under `$TMPDIR`, writes are proposals or governed actions.
- Wired for every shell backend (environment only); proved on `claude-code`.
- **Tests that fail without it**: a generated module per server with one function per tool and its access in
  the docstring; each outcome typed from a scripted bridge answer; a script run under the fakes chains three
  calls and prints only its digest; the instructions are absent when scripts are off.

### S57-09 · feat(evals): the matrix compares the two doors
- `EvalInput.tools: list[str] = ["mcp"]` (the default keeps archived histories replaying; a replayed history is
  recorded); `TOOLS = ("mcp", "scripts")`, forced per cell like `context`; live cases whose run did not get the
  cell's door are skipped.
- Agent cases gain `scripted_usage` per tools cell, and assertions `max_model_requests`, `calls_by_origin`,
  `proposed_actions`, `no_tool_called`, `script_error_rate`; the scenarios of the examples page.
- `publish_matrix` computes, per agent and per pair of cells, a paired bootstrap 95 % interval (10,000
  resamples, fixed seed) of the difference in cost per successful run and in success rate, and a verdict
  (`better`, `not_worse`, `worse`, `inconclusive`).
- **Tests that fail without it**: a scripted matrix where `scripts` is cheaper and as good gives `better`; one
  where it is cheaper and worse gives `worse`; the replayed history of an old matrix still replays.

### S57-10 · feat(web): a run shows its scripts
- On the run page, the calls of one script are grouped under it (its text, digest, calls with status, duration
  and size); proposals made by a script link to their action. Spend shows tool calls by origin. Project policy
  settings show `tools.scripts` and the ceiling.
- **Tests that fail without it**: `apps/web/tests` for the grouping, a proposal link, an absent script text.

### S57-11 · measure: tool scripts on dev, and the decision per agent
- On dev, with `claude-code` and the real gateway: the matrix of the examples page, `mcp` and `scripts`, ten
  repetitions per cell, plus two coding cases as the no-regression control.
- Per agent: the verdict of §5.2. An agent whose cells say `better` gets `tools: {scripts: offer}` as its
  catalogue default (in its plugin); the others stay `off`. STATUS and ADR 0122 say what was measured; the ADR
  becomes `accepted` (or is amended, or superseded if nothing pays).

### S57-12 · Phase 2 — on-demand disclosure
`tools.disclosure: listed | on_demand`: with `on_demand`, the MCP list shows the platform's built-in tools and
`search_tools` / `describe_tool`; connector tools are reached from scripts or after `describe_tool`. Measured as a
third matrix cell; only for runs whose tool list is large (the baseline says how many are).

### S57-13 · Phase 2 — a script that worked becomes a proposed skill
At the end of a successful run, a script whose calls all succeeded may be proposed as a skill (`SKILL.md` + the
script) to the organisation's skill library, through the supply-chain gate of ADR 0050, never installed by
itself.

### S57-14 · Phase 2 — hosted runtimes
The loopback MCP proxy of hosted steps (ADR 0080, port 4002) serves the bridge route to the hosted container,
judged by the same guard rails; the client is written into the shared workspace.

## 3. What a story must show, every time

- `make ci` green; a STATUS row saying what the PR proves and what it does not.
- Each guard has a test that fails without it: the cap under concurrency, the token absent from the agent's
  environment, the origin never used to authorise, the proposal never reported as executed, the bridge absent
  when off, the run token absent from every bridge response and journal line.
- No rule keyed on the door (ADR 0122 §2): a reviewer refuses one.

## 4. Metrics

### 4.1 Per run (recorded by stories 01, 06, 07)

| Metric | Definition | Source |
|:--|:--|:--|
| `model_requests` | model calls of the run | `run.tokens.requests` (story 01), from the gateway's spend logs |
| `tokens_in`, `tokens_cached`, `tokens_out` | input (fresh), cache reads, output | `run.tokens`, ledger `kind="model"` |
| `cost_usd` | model + priced tools | `run.cost_usd`, ledger |
| `platform_tool_calls` | calls that reached `callRunTool` and were booked | ledger `kind="tool"` count |
| `calls_by_origin` | the same, split `model` / `script` / `job` | ledger `dimensions.origin` (story 06) |
| `tool_response_bytes` | sum of `response_bytes` | `choregos.tool.called` (story 01) |
| `scripts`, `script_errors` | scripts that called the bridge; those that raised or exited non-zero | journal `tools.script`, `tools.script.call`, the Bash tool's exit |
| `proposals` | actions proposed by the run, by origin | `actions` table, ledger |
| `agent_turns`, `agent_tool_calls` | as today | `result.diagnostics` |
| `wall_time_s` | as today | `diagnostics.duration_s` |
| `success` | the stage's result passed its DoD and gates; for an eval case, `score ≥ min_score` | `run.status`, the eval judge |

### 4.2 Per agent and per matrix cell (story 09)

| KPI | Formula | Why |
|:--|:--|:--|
| **Cost per successful run** | Σ cost / successful runs | the primary measure: tokens alone overstate the saving because cache reads are cheap |
| Success rate | successful / runs | must not drop |
| Model requests per run (p50, p90) | | round trips: the thing the idea removes |
| Fresh input tokens per successful run | `tokens_in − tokens_cached` | what the gateway bills at full price |
| Wall time (p50, p90) | | latency, which caching does not reduce |
| Leverage | platform tool calls / model requests | > 1 means calls happened without a model turn |
| Script adoption | runs with ≥ 1 script / runs offered scripts | does the model use the door it is given? |
| Script error rate | script errors / scripts | the new failure mode |
| Governance parity | ledger rows = `choregos.tool.called` events = bridge `tools.script.call` + MCP calls | must be exact |

### 4.3 The baseline query (story 01; for the operator, read-only)

`uv run python tools/report_round_trips.py --sql --days 30` prints it; without `--sql`, the same tool computes
the same columns through the API with an operator token (`tools/report_round_trips.py`'s docstring). A value
nobody measured (`tokens.requests` of a run booked before story 01) is null and left out of its percentile.

```sql
with r as (
  select r.id, coalesce(r.agent_slug, r.stage_role) as agent, r.backend, r.status,
         nullif(r.tokens->>'requests','')::numeric                  as requests,
         (select count(*) from cost_ledger l
           where l.run_id = r.id and l.kind = 'tool')               as platform_tool_calls,
         nullif(r.result->'diagnostics'->>'tool_calls','')::numeric as agent_tool_calls,
         nullif(r.result->'diagnostics'->>'turns','')::numeric      as turns,
         nullif(r.tokens->>'tokens_in','')::numeric                 as tokens_in,
         nullif(r.tokens->>'tokens_cached','')::numeric             as tokens_cached,
         nullif(r.tokens->>'tokens_out','')::numeric                as tokens_out,
         r.cost_usd,
         coalesce(nullif(nullif(r.result->'diagnostics'->>'duration_s','')::numeric, 0),
                  extract(epoch from (r.ended_at - r.started_at)))  as wall_time_s
  from runs r
  where r.started_at > now() - interval '30 days' and r.ended_at is not null
)
select agent, backend, count(*) as runs,
       round(avg(case when status = 'succeeded' then 1 else 0 end), 4)     as success_rate,
       percentile_cont(0.5) within group (order by requests)               as requests_p50,
       percentile_cont(0.9) within group (order by requests)               as requests_p90,
       percentile_cont(0.5) within group (order by platform_tool_calls)    as platform_tool_calls_p50,
       percentile_cont(0.9) within group (order by platform_tool_calls)    as platform_tool_calls_p90,
       percentile_cont(0.5) within group (order by agent_tool_calls)       as agent_tool_calls_p50,
       percentile_cont(0.9) within group (order by agent_tool_calls)       as agent_tool_calls_p90,
       percentile_cont(0.5) within group (order by turns)                  as turns_p50,
       percentile_cont(0.9) within group (order by turns)                  as turns_p90,
       percentile_cont(0.5) within group (order by tokens_in)              as tokens_in_p50,
       percentile_cont(0.9) within group (order by tokens_in)              as tokens_in_p90,
       percentile_cont(0.5) within group (order by tokens_cached)          as tokens_cached_p50,
       percentile_cont(0.9) within group (order by tokens_cached)          as tokens_cached_p90,
       percentile_cont(0.5) within group (order by tokens_out)             as tokens_out_p50,
       percentile_cont(0.9) within group (order by tokens_out)             as tokens_out_p90,
       percentile_cont(0.5) within group (order by cost_usd)               as cost_usd_p50,
       percentile_cont(0.9) within group (order by cost_usd)               as cost_usd_p90,
       percentile_cont(0.5) within group (order by wall_time_s)            as wall_time_s_p50,
       percentile_cont(0.9) within group (order by wall_time_s)            as wall_time_s_p90,
       round(sum(cost_usd)::numeric, 4)                                    as cost_usd_total,
       round(avg(case when platform_tool_calls >= 5 then 1 else 0 end), 4) as share_5_platform_calls_or_more
from r group by 1, 2 order by runs desc;
```

### 4.4 Baseline numbers

Not taken yet (story 01). This section receives them.

## 5. Key success criteria

### 5.1 Invariants — must hold at 100 %, each with its test

1. Every call a script makes is booked: one ledger row, one `choregos.tool.called`, one span, its origin
   `script`.
2. No call reaches a connector, a catalogue provider or a plugin server without `callRunTool`.
3. A write under approval made from a script is never executed before a person approves it; the script receives
   a `Proposal`, and the model reads that it is one.
4. The cap holds under concurrent calls (no run exceeds `tool_calls_per_run`).
5. The agent's environment holds no run token on any executor; no bridge answer and no journal line contains it.
6. With `tools.scripts` off (the default), a run is byte-for-byte what it was: no route, no client, no
   instructions, no new StageInput key.
7. No authorisation decision reads the origin.

### 5.2 The gate that turns an agent on by default (story 11)

On the matrix of the examples page, per agent, `scripts` against `mcp`, paired bootstrap 95 % interval:

- cost per successful run: the interval of the relative difference lies entirely below 0;
- success rate: the lower bound of the difference is ≥ −5 points (`not_worse`);
- governance parity exact in every run of both cells.

### 5.3 Targets — what makes the stream worth it

On connector-heavy cases (≥ 5 platform tool calls in the `mcp` cell):

| KPI | Target, `scripts` vs `mcp` |
|:--|:--|
| Cost per successful run | − 30 % or better |
| Model requests per run (p50) | − 50 % or better |
| Wall time (p50) | − 25 % or better |
| Success rate | not lower (5.2) |
| Script adoption when offered | ≥ 60 % of runs |
| Script error rate | ≤ 15 % of scripts, and a failed script costs at most one more model request at p50 |

On the coding control cases: cost and success not worse (5.2) — the door is offered, the model may not use it.

### 5.4 When to stop

- The baseline (story 01) shows fewer than 10 % of production runs with ≥ 5 platform tool calls, and no planned
  agent that would: stories 07-10 are kept to a minimum and phase 2 is not started.
- After story 11, no agent passes 5.2: the feature stays `off`, ADR 0122 records the measurement and is marked
  superseded; the fixes of stories 02-04 and the metrics of 01 and 06 stay, they are worth having anyway.

## 6. Risks and how they are held

| Risk | Held by |
|:--|:--|
| A runaway script floods a connector | the cap (story 03), the bridge's concurrency, the per-host connector rate limits, the gateway budget |
| Allowed writes chained without the model reading between them | the approval floor is the policy's, for both doors; story 10 shows which writes a script made; the matrix's `proposed_actions` assertion |
| The model prints a whole result anyway | the instructions; `tool_response_bytes` vs the Bash output size in the journal; the matrix's cost measure catches it |
| Injection through a printed result | the API's guard on every result (`block` → `Withheld`, `warn` → stderr); the gateway's guard |
| A script committed into the pull request | scripts under `$TMPDIR`, the client git-excluded, the scope hook on workspace writes |
| A second door to secure | no new port, no new credential; the boundary stays the API (ADR 0122 §2) |
| The baseline cannot be read from an agent session | the operator runs §4.3 (story 01) |

## 7. What stays with the operator

- Running the baseline (story 01) on production and pasting the numbers into §4.4: an agent session was refused
  production reads on 2026-11-03.
- Turning the deployment switch on in dev for story 11, and the live gateway budget for the matrix's runs.
- Merges into `main`, the releases that carry the stream, and the decision of story 11 per agent.
