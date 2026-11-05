# Running the live eval matrix

*When:* measuring what rtk and headroom really save, before turning a reduction on by default
(ADR 0055, S37-10), or what tool scripts save, before an agent offers them by default (ADR 0122 §9,
S57-09, S57-11). It spends real model money: a full matrix is backends × models × 4 contexts × 2 doors ×
(6 tickets + the agent cases) × repetitions real stages.

## The matrix's dimensions

`EvalMatrix` plays one cell per backend × model × memory × context × **door** (`tools`), in that
order. Each input has a default that keeps a matrix started before it as it was:

| Input | Values | Default | What a cell gets |
|:--|:--|:--|:--|
| `backends`, `models`, `with_memory` | | `["claude-code"]`, `["platform/standard"]`, `[true, false]` | |
| `contexts` | `none`, `rtk`, `headroom`, `rtk+headroom` | `["none"]` | the context reduction (ADR 0055) |
| `tools` | `mcp`, `scripts` | `["mcp"]` | the door of the run's tools (ADR 0122): `mcp`, the model's MCP tools only; `scripts`, those and the tool bridge with the generated client `choregos_tools` |
| `fixtures` | reference tickets (`tests/fixtures/tickets`) | all of them | |
| `agent_cases` | agent cases (`packages/playbooks/src/choregos_playbooks/evals/agent_cases`) | `[]` | scripted cells (below), and live cells on fake scenes since S57-11a ([Agent cases in a live cell](#agent-cases-in-a-live-cell-s57-11a)) |
| `repetitions` | 1 to 100 | `1` | each case played that many times: the bootstrap's pairs |
| `live` | | `false` | real stages on the eval project's executor |

A live cell **forces** the door into the stage input, whatever the project's policy says, as it forces
the context (`StagePlan.eval_cell.tools`): a `scripts` cell gives `tools.scripts:
{max_concurrent_calls: 4}`, an `mcp` cell gives none. The deployment's kill switch still wins: with
`CHOREGOS_TOOL_SCRIPTS` off (the default), a `scripts` cell is skipped at seeding, and a run prepared
after the switch went off gets no bridge and is skipped as unfaithful. Only a cell with context `none`
**and** door `mcp` validates a backend (`model_profiles.validated_backends`): a run is offered neither
a reduction nor scripts unless its policy says so.

A run that did not get its cell's door is **skipped**, with its reason: a `scripts` run must have been
offered the bridge and its agent must have had its client (`tools.client` written, or a script
journaled); an `mcp` run must have been offered neither. A `scripts` run whose model wrote no script
did get the door: not using it is what `script_adoption` measures.

## Agent cases and the two doors (scripted)

In a scripted cell (`live: false`, CI and `CHOREGOS_FAKES=1`), an agent case goes through the
platform's **real** doors on the fakes (`activities/eval_doors.py`):

- `mcp`: the case's `evidence` calls go through the run's MCP server (`McpServer`, `tools/call`);
- `scripts`: a case with a `scripted_script` runs it as an agent's shell does — a file under the run's
  temporary directory, `python3 -S`, the generated `choregos_tools` on its `PYTHONPATH` — through the
  tool bridge served on the loopback; the script reads the client's outcome types (`Proposal`,
  `Withheld`, `ToolError`). Its answer is the case's `scripted_script.answer` with what the script
  printed in place of `<<stdout>>`. A case without a script goes through the MCP door in this cell too.

Both doors' `InternalClient` talk to a fake API that governs as `callRunTool` does for what the cases
need: the run token, the origin header, the operation's input schema, writes under approval proposed
(202, an action and its decision path), allowed writes executed as an action, the injection guard
(`warn`, `block`), the 64 KiB bound on a result. It has no cap, rate limit, masking or network policy:
the live matrix meets those. The case's `scripted_usage` for the cell is billed on the fake gateway,
as many model calls as its `requests`.

| Case | Scenario | `scripts` cell |
|:--|:--|:--|
| `netops-ssh-exposure` | E1, pfSense SSH exposure through aliases | real script, 8 calls |
| `netops-dropped-flows-top` | E2, Cilium drops, one read per namespace | real script, 13 calls |
| `identity-offboarding` | E4, eight Entra writes under approval (case-local agent) | real script, 8 proposals |
| `marketdata-volatility` | E6, twenty instruments, one computation (case-local agent) | real script, 20 calls |
| `research-injected-page`, `…-block` | E5, six pages, one injected (`warn`, `block`) | evidence-scripted (MCP door) |
| `py-rounding-bug`, `py-volume-discount` | C2, the coding control | the ticket's scripted change |

E3 and C1 need the ServiceNow plugin server, which lives in `choregos-agents`: their cases belong in
its `evals/` directory (#1372). E5 has no script because a script's `web_fetch` gets the fetch's
metadata, not the page (#1373); the MCP door shows a model less of a large answer than a script reads
(#1374), which S57-11 must keep in mind when it reads the `mcp` cells' success rates.

A case's file: `connectors` (each one's fake and its scene, `choregos_adapters.fakes.scenes`), `policy`
(`writes: approval | allowed`, `prompt_injection: ignore | warn | block`), `web` (the scripted search
results and pages of `web_search` and `web_fetch`), `agent_spec` (a case-local agent's instructions),
`tools_cells`, `scripted_usage` per door, `scripted_script`, `reference_answer` (the `mcp` cell's), and
the `assertions` below. A ticket's `scripted_usage` is keyed by context for its `mcp` cell and by
`<context>/<tools>` (`none/scripts`) for another door.

### What a case asserts about its run

Beside its answer (`answer_must_contain`, `answer_must_not_contain`, `answer_sections`, `min_score`),
an agent case asserts what its run did (`choregos_playbooks.evals.agents.check_assertions`). An
assertion with a `cell` is checked in that cell only; `count` is exact, `min` and `max` bound.

| Assertion | Holds when |
|:--|:--|
| `max_model_requests: {max}` | the run's model calls (the gateway's) are at most `max`; an unmeasured run fails it |
| `calls_by_origin: {origin, count \| min \| max}` | the calls that reached the platform through that door (`model`, `script`, `job`) |
| `proposed_actions: {tool?, count \| min \| max}` | actions waiting for a person (`pending_approval`); `tool` is a glob |
| `executed_actions: {tool?, count \| min \| max}` | actions whose write was played (`running`, `awaiting_evidence`, `succeeded`, `failed`) |
| `no_tool_called: {tool \| tools}` | no call to these tools (globs), through either door |
| `security_event: {type, count \| min \| max}` | the platform's security events of that type; at least one without a bound |

A case passes when its score reaches `min_score` **and** every assertion holds.

## Before

1. An **eval project**, used for nothing else:
   - a repository the platform's SCM app can write to, with a default branch holding at least one
     commit (a README is enough). The toy repositories are committed to
     `choregos-evals/<repo>-<digest>` branches of it, and each run pushes its work branch there;
   - the **internal** tracker (no tracker connector, or `internal`): eval work items never reach an
     external tracker, a cell with another tracker is skipped;
   - memory **off** in its policy; its `context` does not matter, the cell decides;
   - the backends and models to measure allowed by its policy and the gateway.
2. The deployment allows context reduction: `runner.contextReduction.enabled: true` (the default).
   With it off, every cell but `none` is skipped, and says why.
3. The runner image carries rtk and headroom (`docker/runner.Dockerfile`, built by `make images`; ADR 0055).
4. For the agent cases: an eval organisation of its own, `CHOREGOS_EVAL_FAKE_SCENES=1` on the API and
   the workers, and `netops` installed ([What the operator provides](#what-the-operator-provides-on-dev)).

## Run

```bash
temporal workflow start --task-queue orchestrator --type EvalMatrix \
  --workflow-id evals-live-$(date +%Y%m%d) \
  --input '{"project_slug": "evals", "backends": ["claude-code"],
            "models": ["platform/standard"], "with_memory": [false],
            "contexts": ["none", "rtk", "headroom", "rtk+headroom"],
            "live": true, "publish": true}'
```

`with_memory: [false]` is required: a live cell with memory is skipped. `publish: true` writes the
cells to `GET /models/matrix` and the settings page.

To compare the doors, add `"tools": ["mcp", "scripts"]` and `"repetitions": 10`, with the deployment's
`choregos-orchestrator.runner.toolScripts.enabled: true` (`CHOREGOS_TOOL_SCRIPTS`). To play the agent
cases too (S57-11's matrix, the examples page), name them and switch the eval fakes on (next section):

```bash
temporal workflow start --task-queue orchestrator --type EvalMatrix \
  --workflow-id evals-live-tools-$(date +%Y%m%d) \
  --input '{"project_slug": "evals", "backends": ["claude-code"],
            "models": ["platform/standard"], "with_memory": [false], "contexts": ["none"],
            "tools": ["mcp", "scripts"], "repetitions": 10,
            "fixtures": ["py-rounding-bug", "py-volume-discount"],
            "agent_cases": ["netops-ssh-exposure", "netops-dropped-flows-top",
                            "identity-offboarding", "marketdata-volatility"],
            "live": true, "publish": true}'
```

That is 2 doors × (2 tickets + 4 agent cases) × 10 repetitions = 120 real stages. The two tickets are
C2, the coding control; E5 (`research-injected-page*`) is left out: a live cell skips it (#1378).

## Agent cases in a live cell (S57-11a)

A live cell plays an agent case as a real stage of its agent, on the real gateway, against connectors
that are **fake scenes** (`choregos_adapters.fakes.scenes`): no real firewall, cluster or directory is
ever reached. `seed_eval_ticket` provisions it, idempotently (a retry and the next repetition find what
the first one wrote), in an **eval project of its own** beside the matrix's eval project, in its
organisation (`apps/orchestrator/src/choregos_orchestrator/eval_live_cases.py`):

- the project `<eval project>-<case>` (`evals-netops-ssh-exposure`): the eval project's configuration
  without its repository, GitOps, schedules, triggers and start rules; its `runtime` and `gateway`
  connectors (references); its policy with memory off, no `tools` section (the cell forces the door) and
  the case's `prompt_injection`;
- one **organisation connector** per connector of the case, named as the case names it (`pfsense`,
  `cilium`, `entra`, `marketdata`), of the type its kind's scenes run (`pfsense`, `cilium`, `entra`,
  `composite`), its configuration `{fake_scene: <scene>}` and nothing else (no address, no secret),
  `selected_projects` and turned on for the case's project alone. Its operations are its type's, `allowed`
  at the organisation; with the case's `writes: approval` (the default) the case's project tightens every
  write to `approval`, so a write is proposed, never played;
- the agent: a **catalogue** agent (`netops`) is resolved as for any run, so the organisation must have
  installed it; a **case-local** one (`agent_spec`: `identity-operator`, `analyst`) is registered as a
  project agent of the case's project, its connectors the case's, `workspace: none`, `memory: none`, a new
  version only when its spec changes;
- the work item: the case's `task`, `EVAL-<digest>` of the matrix run, the cell and the repetition,
  labels `eval` and `eval:<case>`, in the internal tracker.

**Which connectors a fake serves.** The API builds a connector whose configuration holds `fake_scene` from
that scene instead of its type's adapter (`services/plugin_servers.connector_client`), and only when the
deployment says so: `CHOREGOS_EVAL_FAKE_SCENES=1`. Without it the field is ignored, the connector is
built as its type says, from a configuration that points nowhere, and the seeding skips every agent case
(`the deployment does not serve eval fakes`). No person, project or organisation can set the field: every
declaration and edit of a connector refuses it (422, `registry.validate_config`), whatever the deployment
allows; only the seeding, which writes its rows itself, does.

**What is never touched.** A connector of the case's name that is not the matrix's (another type, another
scene, a secret, an address, another author), or an agent of the case-local slug that is not the case
project's, makes the case skip with its reason: the seeding does not rebind it. Use an **eval
organisation** of its own, where the connector names and agent slugs are free.

**How it is judged.** The run's answer is its output (`case.output`, else its summary), a decision path
`/p/<case project>/actions/…` read as the scripted matrix's `/p/evals/actions/…`; what the run did is read
from the API: its `choregos.tool.called` events (calls by origin), its actions (proposed, executed), its
security events, its journal's scripts, its model calls (`tokens.requests`). The case's judge
(`choregos_playbooks.evals.agents.judge`) checks `answer_must_contain`, `answer_must_not_contain`,
`answer_sections`, `min_score` and every assertion of the case (`max_model_requests`, `calls_by_origin`,
`proposed_actions`, `executed_actions`, `no_tool_called`, `security_event`). A run that did not get its
cell's door or context is skipped, as a ticket's is.

**Governance parity.** Before it is judged, a live agent-case run must agree with itself on its tool
calls (the plan's §4.2): its booked `tool` lines of the ledger (an `in_flight` line is not a call) equal
its `choregos.tool.called` events, per tool and origin; every event of a call from a script has its tool
bridge `tools.script.call` line (same tool, digest and outcome); a bridge line without an event is a call
the API refused before booking it (404, the cap, a refused schema, a timeout: 400 or more), never one it
served. A run that breaks it is **invalid**: its line in `details` says `invalid: true` and why, the
cell's `invalid` counts it, and it is in no rate, no median and no pair of the comparison. A call the
network policy refused has an event and no ledger line (ADR 0070) and reads as a mismatch; the fake
scenes reach no network.

**What a live cell does not play.** E5 (`research-injected-page`, `…-block`): its tools are `web_search`
and `web_fetch`, which read the internet from the API, not the case's `web:` pages (#1378). A case with no
connector (`netops-dropped-flow`, the netops agent's own eval). E3 and C1 live in `choregos-agents`
(#1372).

### What the operator provides, on dev

1. The deployment's switches, on the API **and** the orchestrator's workers (`global.extraEnv` gives a
   variable to both):

   ```yaml
   global:
     extraEnv:
       - name: CHOREGOS_EVAL_FAKE_SCENES
         value: "1"
   choregos-orchestrator:
     runner:
       toolScripts:
         enabled: true        # CHOREGOS_TOOL_SCRIPTS: the `scripts` cells
       contextReduction:
         enabled: true        # only for cells with a context other than `none`
   ```

   Switch `CHOREGOS_EVAL_FAKE_SCENES` off again once the matrix is done: while it is on, a connector the
   seeding wrote is served by its scene.
2. An **eval organisation** holding the matrix's eval project (`Before`, above) and nothing else, so
   that the names `pfsense`, `cilium`, `entra`, `marketdata` and the slugs `identity-operator`,
   `analyst` are free in it.
3. The catalogue agent `netops` **installed** in that organisation (Agents → Catalogue), on a backend
   and a model the eval project's policy allows. Without it the netops cases skip, saying so.
4. The real model gateway the eval project runs on (its connector, or the organisation's, or the
   deployment's): the agent cases' cost is what it bills.

## Read it

### What a cell says about its tools

Over the runs with an observation (a live run's events, ledger and journal; a scripted case's fake API):

- `median_requests`: model calls per run (S57-01); `median_platform_tool_calls`: calls that reached
  the platform per run; `calls_by_origin`: the cell's calls by door;
- `script_adoption`: the share of runs with at least one script;
- `scripts`, `script_errors`, `script_error_rate`: a script is a digest the tool bridge journaled
  (`tools.script`, or named by a `tools.script.call`); it is **in error** when one of its calls was
  answered 400 or more (the client raised `ToolError`, `CapReached` or `Withheld`), or when its exit
  code is known and is not 0 (scripted cells only: a live run's exit code is the agent's shell's). The
  rate is errors over scripts, `null` without a script; a script that never called the bridge is not
  counted;
- `proposed_actions`, `executed_actions`: the cell's actions waiting for a person, and those played;
- `invalid`: the live runs whose governance parity broke (S57-11a), out of every number above.

Each case's line in `details` carries its `repetition`, its `agent` (the case's, or `role:<role>` for
a ticket), its `cost_usd` (`null` when the gateway counted nothing) and its `door` (`bridge` when a
script ran through the bridge, `mcp` otherwise).

### The two doors compared

`publish_matrix` compares every pair of cells that differ only by their door (`mcp` against
`scripts`, same backend, model, memory and context), per agent and per case, and writes the
comparisons beside the cells, in the model profile's `params.matrix_comparisons` (the API's
`GET /models/matrix` is unchanged). `eval_comparison.py` holds the definitions:

- a **pair** is a case and a repetition played, not skipped, in both cells;
- the statistics: the **difference in success rate** (`scripts` − `mcp`) and the **relative difference
  in cost per successful run** (`cps(scripts) / cps(mcp) − 1`, `cps = Σ cost / successful runs`). A cell
  without a success has an infinite cost per success: `+∞` when only `scripts` has none, `−1` when only
  `mcp` has none, 0 when neither has one;
- the **interval**: a paired bootstrap, 10,000 resamples of the pairs with replacement, the same
  resample for both statistics, drawn from `random.Random(5709)`'s `random()` only (deterministic on
  every machine); the 95 % interval is the nearest-rank 2.5th and 97.5th percentiles (ranks 250 and
  9,750). An infinite bound is published as `null`;
- a pair whose run was not metered in both cells has an unknown cost, never a cost of 0: the cost
  interval is then not computed, and the verdict can only be `worse` by success, else `inconclusive`.

The **verdict**, checked in this order, with a margin of five points of success rate:

| Verdict | When |
|:--|:--|
| `worse` | the success interval's upper bound is below −0.05, or the cost interval lies entirely above 0 |
| `better` | the cost interval lies entirely below 0 and the success interval's lower bound is ≥ −0.05 |
| `not_worse` | the success interval's lower bound is ≥ −0.05 and the cost interval holds 0 |
| `inconclusive` | anything else, no pair, or an unmetered pair the success does not call `worse` |

An agent whose comparison says `better` on the matrix of the examples page meets the gate of
ADR 0122 §9 (with governance parity, which S57-11 checks on the live runs). In a scripted cell the
numbers are the cases' illustrative `scripted_usage`: a scripted verdict proves the computation, not the
saving.

- `temporal workflow show --workflow-id evals-live-…`, or the settings page's matrix: per cell, the
  success rate, the medians of input, cache-read and output tokens, cost and turns, over the
  metered cases only.
- Each case is a work item `EVAL-<digest>` with label `eval:<ticket>` and one run: its page shows
  the context reduction card (what ran, what headroom says it removed, *inferred*) next to the
  gateway's cost (*observed*).
- The cell's cost is the gateway's. Check first that the gateway gives a price to cache reads and
  writes (`cache_read_input_token_cost`, `cache_creation_input_token_cost` in the model's
  `model_info`). Without them, a Claude Code run, which reads most of its input from the cache,
  costs mostly its output. That is the result the 2026-10-19 matrix got: ADR 0055 says how it was
  priced again from the spend logs. Both gateways price the cache since then (#737).
- A `skipped` case is not a failure and is not in the rate. Its reason says why: a tool missing on
  the runner, headroom unavailable, a backend without a shell-filter hook (rtk on anything but
  Claude Code), a stage that could not be prepared.

## After

Write the numbers and the per-backend decision in ADR 0055 (rule: a lower cost **and** a success
rate not lower, within the matrix's confidence interval). The eval work items stay, labelled
`eval`; the `choregos-evals/*` and work branches can be deleted from the eval repository.
