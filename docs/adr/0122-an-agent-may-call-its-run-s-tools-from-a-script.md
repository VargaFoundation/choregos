# 0122 — An agent may call its run's tools from a script: the runner relays, the API governs each call, the model reads what the script prints

- **Status**: accepted, 2026-11-07 (product owner, on the measurement of S57-11: tool scripts on by default for
  the agents that passed the gate of §9, netops alone, and off for identity-operator and the write-heavy agents);
  proposed 2026-11-03 (product owner: "instead of 36 round trips between the MCP servers and the model, the
  agent writes a piece of code, runs it all, and the model analyses the final answer — is it worth it in
  Choregos?"); plan [`PLAN-2026-11-03-tool-scripts.md`](../plan/PLAN-2026-11-03-tool-scripts.md), stream S57,
  implemented by S57-01 to S57-11c; §6 amended 2026-11-05 (S57-05b: the organisation decides, the deployment's
  switch is its default, a hard brake stays); §9 amended 2026-11-07 (S57-15: the measurement, the per-agent
  default and its precedence)
- **Concerns**: `policy.schema.json` (a top-level `tools` section), `stage-input.schema.json`
  (`tools.scripts`), `getRunTools` (tool annotations) and `callRunTool` (the origin headers), the
  `choregos.tool.called` event (`origin`, `script_sha256`, `response_bytes`), the runner's local tool server
  (`choregos_tools_mcp.app`, `choregos_runner.local_tools`), a generated client `choregos_tools`, the eval
  matrix (`workflows/evals.py`, a `tools` dimension); builds on
  [ADR 0004](0004-cost-counted-at-the-gateway.md), [ADR 0010](0010-gates-are-mechanisms.md),
  [ADR 0035](0035-governed-actions-in-the-core.md), [ADR 0055](0055-fewer-tokens-per-run.md),
  [ADR 0070](0070-network-access-is-bounded-and-enforced-per-run.md),
  [ADR 0084](0084-guardrails-outside-the-agent.md), [ADR 0094](0094-the-kernel-confines-the-agent-at-launch.md),
  [ADR 0096](0096-a-plugin-s-code-runs-as-an-mcp-server-the-platform-runs.md),
  [ADR 0101](0101-governance-the-platform-does-not-enforce-is-refused.md) and
  [ADR 0115](0115-the-agent-never-holds-the-run-token.md)

## Context

Every platform tool an agent uses (`<connector>__<operation>`, a catalogue tool, a plugin server's tool,
`web_fetch`, `web_search`) is one model turn: the model writes the call, the runner's local MCP server relays it
to `POST /internal/runs/{id}/tools/{name}`, the API governs it, and the whole result comes back into the
model's context. Two costs follow, both described in Anthropic's engineering note "Code execution with MCP"
(2025-11-04):

1. **Intermediate results pass through the model.** A firewall's rule set, a flow log, a list of incidents
   enters the context in full (up to 64 KiB per result, `broker_result_max_bytes`), often only to be copied
   into the arguments of the next call or reduced to three lines.
2. **Each round trip re-reads the whole context.** Ten chained calls are ten model requests over a context
   that grows at each one. Prompt caching makes the re-read cheaper (cache reads are billed about a tenth of
   fresh input), not free, and it does not shorten the wall clock.

The alternative the note describes: the tools are presented to the model as a code API, the model writes one
script that chains the calls, filters and joins their results, and prints only what the next decision needs.
The script runs in the agent's sandbox; the model reads its output.

What exists today, on `main`:

- **The agent already runs code.** Every shell backend (`claude-code` by default, `codex`, `gemini-cli`,
  `goose`, `opencode`, `copilot-cli`) has a shell inside the run's sandbox: gVisor or Landlock (ADR 0094),
  the egress proxy (ADR 0070), the run's writable roots. A script adds no new runtime and no new pod.
- **The local tool server is not a boundary.** The runner serves the platform's tools on loopback
  (`localhost:7777/mcp`, `local_tools.py`), and the server asks the caller for nothing
  (`choregos_tools_mcp/app.py`). Any process in the sandbox can post a JSON-RPC `tools/call` to it today;
  `NO_PROXY` and the confinement's port set already let it through. Such a call skips the runner's
  permission check, its turn counter and its journal. **The API still governs it in full.**
- **The API governs each call, whoever sends it.** `callRunTool` resolves the tool against what the run is
  allowed, counts it against `budgets.tool_calls_per_run`, turns a write under approval into a proposal
  (ADR 0035), plays an allowed write as a governed action with an idempotent call id, bounds and masks the
  result, runs the injection guard (`sandbox.prompt_injection`), writes a `kind="tool"` ledger row and a
  `choregos.tool.called` event, inside an `execute_tool` span.
- **The MCP route loses part of the answer.** The tool server returns only `result`, cut at 20,000
  characters, and marks an error only from status 400. A `202` proposal reads as a success; `remaining` and
  `suspicions` are dropped.
- **The cap check races.** `callRunTool` counts the run's ledger rows, then inserts its own row later,
  without a lock: concurrent calls can each pass the check.
- **The agent may still hold the run token.** ADR 0115 keeps the run token out of the proxy variables, but
  under the `k8s_job`, ACA and local Docker executors the runner's container receives `CHOREGOS_RUN_TOKEN` in
  its environment, and the agent inherits the runner's whole environment (`AcpClient.env = {**os.environ,
  …}`). The `agent_sandbox` executor keeps the token in memory only.
- **Nothing measures round trips.** The gateway knows the number of model requests per run
  (`Spend.requests`), the orchestrator does not persist it; `choregos.tool.called` carries no size; the eval
  matrix scores cost, tokens and turns but computes no confidence interval in code.

## Decision

1. **A script may call the run's tools, through the runner, never around it.** When the effective policy
   offers it (§6), the runner serves a second route on its local tool server, the **tool bridge**:
   `GET /bridge/v1/tools` (the run's tools, as `getRunTools` lists them) and `POST /bridge/v1/tools/{name}`
   (the arguments as the body). The bridge relays to `callRunTool` with the runner's run token and returns
   the API's answer **unchanged**: `status_code`, `result`, `remaining`, `suspicions`. The agent holds no
   credential for the API; the bridge is the runner's.
2. **Governance does not depend on the door.** Every rule of `callRunTool` applies to a call from a script
   exactly as to a call from the model: the allowlist, the cap, the approval floor, governed writes and their
   idempotent call ids, bounds, masking, the injection guard, the ledger, the event, the span. No rule is
   relaxed for scripts, and **no rule is added that only scripts obey**: the local tool server cannot tell a
   script from the model's MCP client (both are processes of the agent's sandbox, and whatever the MCP client
   is told, the agent's shell can read), so a rule keyed on the door would be governance the platform does
   not enforce (ADR 0101). The door is recorded as **attribution**, never used as a guard (§4).
3. **A generated client, standard library only.** The runner writes, per run and outside the workspace
   (`$TMPDIR/choregos/tools`, on `PYTHONPATH` and named by `CHOREGOS_TOOLS_DIR`):
   - `choregos_tools/__init__.py`: `call(name, **arguments)`, `search(query, detail="name"|"summary"|"schema")`,
     and the typed outcomes below;
   - `choregos_tools/<server>.py`: one function per tool the run may call, its docstring the tool's
     description, its access (`read`, `write`, `approval`) and its input schema;
   - `INDEX.md`: one line per tool (name, access, first sentence of its description).

   The outcomes are typed so that a script cannot mistake one for another: a `200` returns the result; a
   `202` with an `action` returns a `Proposal` (`action`, `status`, `decision_path`) — **proposed, not
   executed**; `429` from the cap raises `CapReached`; `451` raises `Withheld`; any other status of 400 or
   more raises `ToolError` with the status and the API's body. `suspicions` on a result are written to the
   script's standard error, so the model reads that the guard flagged it, whatever the script prints.
4. **The door is recorded.** The bridge sends `X-Choregos-Tool-Origin: script` and, when the client knows
   it, `X-Choregos-Script: <sha256 of the script>`; the MCP route sends `origin: model`; a job step
   (ADR 0056) is `origin: job`. The API writes the origin and the digest on the ledger row's dimensions, on
   `choregos.tool.called` (with `response_bytes`) and on the span (`choregos.tool.origin`). The first call of
   a script sends its text once (at most 64 KiB): the runner journals it as `tools.script` (`sha256`,
   `bytes`, `text`), and every relayed call as `tools.script.call` (`tool`, `status_code`, `duration_ms`,
   `response_bytes`, `script_sha256`). An audit reads which code made which calls.
5. **What the model reads is what the script prints.** Results stay in the sandbox unless printed. The
   instructions the runner adds when the bridge is offered say when a script pays (three or more dependent
   calls, a loop, a result to filter or join), that a script prints only what the next decision needs,
   never a secret, and that a write from a script is a proposal or a governed action like any other. The
   model keeps its MCP tools: it chooses, call by call, between a tool call and a script (**hybrid**). The
   gateway's LLM guard (ADR 0084) still masks what reaches the model.
6. **Off by default, three switches, the strictest wins** (the shape of ADR 0055 §4):
   - the deployment: `CHOREGOS_TOOL_SCRIPTS=off` (Helm `choregos-orchestrator.runner.toolScripts.enabled`)
     turns it off whatever a policy says;
   - the organisation's ceiling may forbid it (`tools.scripts: off` in the ceiling);
   - the policy: `tools.scripts: off | offer`, `tools.max_concurrent_calls` (default 4), and `per_role`
     overrides; an absent `tools` is `off` (since §9's amendment of 2026-11-07: an absent `scripts` leaves
     the agent's default, else `off`). The orchestrator resolves it into `StageInput.tools.scripts`,
     and leaves the key out when it is off, so an older runner never sees it.

   **Amended 2026-11-05** (S57-05b; product owner: "make `runner.toolScripts.enabled` configurable at the
   organisation level"). The first switch is now the **organisation's**: its setting `tool_scripts`
   (`enabled`, `disabled`, or absent), a key of `Organization.settings` written by its administrator alone
   with `PATCH /orgs/{org}` (a platform administrator who is not one is refused) and audited in `org.update`.
   Absent, the organisation follows the deployment's `CHOREGOS_TOOL_SCRIPTS` (Helm
   `choregos-orchestrator.runner.toolScripts.enabled`), which becomes the **default** of an organisation that
   does not choose — still off unless the operator turns it on. So an organisation's `enabled` wins over a
   default that is off, and its `disabled` over a default that is on: on that switch the strictest no longer
   wins, the organisation decides. A **hard brake** stays with the operator: `CHOREGOS_TOOL_SCRIPTS_ALLOWED=off`
   (Helm `…toolScripts.allowed: false`; `true` by default) refuses tool scripts to every organisation whatever
   its setting says — an emergency stop, and the protection of a deployment whose runner image predates the
   bridge. Past the switch the ceiling (which only forbids) and the policy (`offer`, per role) apply as
   before, and an eval cell's door never runs against it. The orchestrator reads the setting with the
   project's organisation, in the bundle it already loads for every stage, so the choice costs no query;
   the API and the orchestrator read and write it through one module (`choregos_core.tool_scripts_switch`).
   The API does not know the deployment's default: the console says "the deployment's default" without
   its value.
7. **Bounded where it can be.** The bridge runs at most `max_concurrent_calls` calls of a run at once. The
   API takes a per-run transaction lock before it counts the run's tool calls, so the cap holds under
   concurrency. Money is still capped where it is counted: the run's gateway key for the model (ADR 0004),
   `check_after_spend` for priced tools. A script runs under the agent's own command timeout and the
   run's wall-clock limit; a script that polls (`get_status` until `FINISHED`) is legitimate and is bounded
   by the cap, not by the runner's loop guard, which counts the model's tool calls only.
8. **Preconditions, fixed first.** The agent never holds the run token on any executor (the runner drops
   `CHOREGOS_RUN_TOKEN` and `CHOREGOS_RUN_REMOTE_TOKEN` from the agent's environment, with a test per
   executor); the cap check takes its lock; the MCP route keeps the answer's meaning (a proposal says it is
   one, `remaining` and `suspicions` reach the model). These hold for the model's calls as much as for
   scripts, and the comparison of §9 is only fair once both doors say the same thing.
9. **Default on only after measurement, per agent.** The eval matrix gains a `tools` dimension (`mcp`,
   `scripts`) beside `context`. An agent's catalogue entry may make `offer` its default only when its cells
   show, with a paired bootstrap 95 % confidence interval computed by the matrix itself, a lower cost per
   successful run **and** a success rate that is not lower. Until then nothing changes for a project that
   does not ask.

   **Amended 2026-11-07** (S57-11, S57-15; the product owner approved the decisions below). **The
   measurement.** Matrix `evals-live-tools-s57-11-20261107` on dev: core 0.24.0, `opencode`,
   `platform/claude-opus`, no memory, no context reduction, the `mcp` and `scripts` cells, ten repetitions of
   E1 `netops-ssh-exposure`, E2 `netops-dropped-flows-top`, E4 `identity-offboarding` and the coding control
   `py-volume-discount` (80 runs). Governance parity held in every run; no script failed; 120 actions were
   proposed and none executed in each cell. The comparisons the matrix published on dev predate S57-11c,
   whose rules stopped failing right answers that named a decoy as what it is; the numbers below are those
   outcomes re-judged with S57-11c's rules (`compare_cells`, the paired bootstrap of plan §5.2), not the
   published ones:

   | Agent | Cost per successful run, `scripts` vs `mcp` (95 % CI) | Success rate | Verdict |
   | :-- | :-- | :-- | :-- |
   | `netops` (E1 + E2) | −38.0 % [−49.3 %, −23.3 %] | +0 [0, 0] | `better`: passes the gate |
   | — E2 `netops-dropped-flows-top` | −58.4 % [−63.8 %, −52.6 %] | +0 | `better` |
   | — E1 `netops-ssh-exposure` | −1.3 % [−13.3 %, +12.0 %] | +0 | `not_worse` |
   | `identity-operator` (E4) | +13.1 % [+2.6 %, +24.9 %] | +0 | `worse`: fails it |
   | coding control | +0.2 % [−8.8 %, +8.3 %] | +0 | `not_worse` |

   **The decision, per agent.** `netops` makes `offer` its default (its plugin's 1.1.0, `spec.tools: {scripts:
   offer}`); `identity-operator` and the write-heavy agents do not (its eight proposed writes cost more
   through a script); every other agent stays without a default until its own cells pass. What was not
   measured: any other model or backend (`claude-code` is not served on dev), cases E3, E5, E6, C1 and C2,
   production. A later matrix on another model may revise a default; a default is changed in the agent's
   plugin, not in the core.

   **The mechanism.** An agent version may declare `spec.tools: {scripts: off | offer}` (absent: no default;
   `off` is the same as absent), left out of its stored spec while unset so no published version's digest
   moves, and refused with an `a2a` runtime. For each stage the orchestrator decides, strictest first: the
   deployment's brake; the organisation's switch (§6, as amended); the organisation's ceiling when it forbids
   them, asked of the run itself because a policy that says nothing is not clamped; **the policy when it says
   something for the stage's role** — its `per_role.<role>.scripts`, else its `tools.scripts`; the agent's
   default; else off. `max_concurrent_calls` is the policy's (its role's, else 4). An eval cell's door is the
   cell's, whatever the policy or the agent says. To say nothing, the policy's `tools.scripts` now defaults to
   `null` (absent) instead of `off`, and is never written out when `null`. Nothing a stored policy says
   changes: one stored by 0.23.0 to 0.24.0 holds the `off` that was then the default, written out whether or
   not its author wrote it, and that `off` still beats an agent's `offer` until the policy is saved again from
   a YAML that does not write `scripts`. A plugin that ships the field requires a core that knows it
   (`core: ">=0.24.1"`), since an older core refuses an unknown spec field.
10. **Phase 2 is a separate decision.** Listing only a few tools to the model and letting it discover the
    rest from the client (`tools.disclosure: on_demand`), turning a script that worked into a proposed skill
    (through the gate of ADR 0050), and offering the bridge to hosted runtimes (ADR 0080, through the loopback
    MCP proxy) are each measured and decided after phase 1's numbers, not before.

## Consequences

- On connector-heavy runs (netops, ITSM triage, identity, research over many pages), the chained calls and
  their raw results leave the model's context; the model reads a digest. The size of the saving is not known
  until the baseline (stream S57, story 01) and the matrix (story 09) measure it.
- Coding runs change little: their agent already scripts with its shell, and most of their tokens are files
  and test logs, which ADR 0055 addresses.
- A run's journal and the console show calls grouped by script, with the script's text: more to read for an
  auditor, and the first record of *why* a burst of calls happened.
- The model sees less, so it judges less between calls: a script decides blind on intermediate results. A
  write from a script still meets the same floor — a person approves what policy says a person approves — but
  an allowed write chained in a loop is no longer read by the model between two iterations. The cap and the
  per-host connector rate limits bound it; a policy that wants a person on those writes already says
  `approval` for them, for both doors.
- Script errors become a new failure mode (an unexpected result shape, an exception). The matrix counts
  them (`script_error_rate`), and the model can read the traceback and fix its script in one more turn.
- The client is regenerated per run from `getRunTools`; a tool added mid-run is not in it until the next run.
  `call(name, …)` reaches it anyway.
- Several numbers become available for every run, scripts or not: model requests (`tokens.requests`), tool
  calls by origin, response bytes.

## Alternatives rejected

- **Programmatic tool calling inside the provider's sandbox** (code that the model provider runs and that
  pauses to call the client's tools). Tied to one provider, while an organisation brings its own gateway
  (ADR 0109); it would run the code and hold the intermediate results outside the deployment; Claude Code and
  the other backends do not use it.
- **A dedicated code-execution sandbox per run (a second pod or container).** The agent's own sandbox is
  already confined, and already runs the agent's code; a second one doubles the warm pool for no isolation
  the first lacks.
- **Handing the script a token for the API** (the way a job step receives `CHOREGOS_RUN_TOKEN`). It would
  give the agent the run's whole internal API, which ADR 0115 took away; the bridge relays the one route.
- **Stricter rules for scripts only** (for instance: every write from a script under approval). Not
  enforceable: the same call reaches the API through the MCP route, which the agent's shell can also reach
  (§2). A write that needs a person is declared `approval` in policy, for every door.
- **Authenticating the local tool server with a per-run secret.** Whatever secret the agent's MCP client
  holds, its shell can read; it would look like a boundary and be none. The boundary is the API.
- **Removing the per-call tool path and keeping scripts only.** Judgment between calls is often the work
  itself (triage, diagnosis); the model chooses, and the matrix says which choice pays.
- **TypeScript or both languages for the client.** Python is in every runner image and every shell backend
  runs it; a second language is added when a backend that lacks Python asks for it.
