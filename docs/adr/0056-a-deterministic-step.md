# 0056 — A deterministic step: the `job` actor

- **Status**: accepted, 2026-10-14
- **Concerns**: the workflow contract (`packages/contracts`, `workflow.schema.json`,
  `stage-input.schema.json`), the DSL engine and validator, the orchestrator's stage preparation, the
  runner; builds on [ADR 0005](0005-adaptateurs-et-fakes.md) (fakes),
  [ADR 0008](0008-idempotence-et-run-id.md) (idempotence),
  [ADR 0045](0045-la-provenance-d-un-signal.md) (where a signal comes from) and
  [ADR 0049](0049-an-artifact-is-typed-stored-and-traced.md) (artifacts)

## Context

Every step a runner executed was an agent: a model, a gateway key, a prompt, a loop. Some steps are
not judgement but computation — a backtest, a data snapshot, a conversion, a statistical test. Run
by an agent, they have three defects that no gate can repair:

- **the numbers are declared, not measured**: an agent asked for a Sharpe ratio may compute one,
  approximate one or invent one, and `evidence.facts` says only what it wrote (ADR 0045);
- **they are not reproducible**: the same inputs do not give the same outputs, so a result cannot be
  replayed or audited;
- **they cost a model**: tokens are spent to run code that needs none.

The investment research use case (S38) made this blocking: its verdict ("is this product worth
anything?") rests on metrics that must be computed by code, the same way every time.

## Decision

1. **A fourth actor type, `job`**: `{type: job, role, command, image?, max_minutes?, tools?}`. `command` is an
   **argv** (1 to 64 strings), never a shell line: no word splitting, no expansion, no injection from
   a work item's fields. `image` is the **name** of an image the platform knows
   (`CHOREGOS_JOB_IMAGES`, a name → reference map set by whoever operates the platform), never a free
   image reference: what runs in a pod is not chosen by whoever writes a workflow. Without `image`,
   the job runs in the runner image.
2. **A job is a run.** It shares an agent step's lifecycle: a `Run` row, a run token, admission, the
   declared inputs and outputs and their types, the repository, the permissions, the `run.queued`
   event, start, await, collect, gates, `on_fail`. The `*agent` wildcard covers a job's state
   (`RUN_ACTORS`), and a backwards job transition needs a bound on its retries like an agent's.
3. **What a job does not get**: no model is resolved, **no gateway key is minted** (a job that cannot
   reach a model cannot spend on one), no playbook, no memory, no MCP tools, no DoD repair loop
   (`dod_iterations: 0`). `run.backend` is `job`, `run.model` is empty, `playbook_checksum` records
   `job:<image>@sha256:<argv digest>`.
4. **The contract with the command**: the runner writes the work item's `fields` to
   `.choregos/fields.json`, runs the command with `CHOREGOS_WORKSPACE`, `CHOREGOS_RUN_ID`,
   `CHOREGOS_API_URL` and `CHOREGOS_RUN_TOKEN` (the run's own short-lived token, to call a connector
   through the internal API — never a provider key), and a timeout of `max_minutes`. The command
   writes `.choregos/job.json` = `{summary, facts, outputs}` and its file outputs in
   `.choregos/outputs/` (ADR 0049).
   A job reaches the outside **only** through the connector operations its actor names:
   `tools: [{connector, tools: [operation patterns]}]` (at most 16 connectors). The internal API's
   tool broker reads them from the run's stage input, the way it reads an agent's `mcp_servers`
   from the registry; a job that names none sees no tool.
5. **The exit code is the status.** Non-zero is `failed` (`job_exit`, or `timeout`); an unreadable
   `job.json`, a fact that is not a plain value (NaN included), or an output the contract refuses is
   `job_result_invalid`; a declared inline output that is absent is `missing_outputs`. A job's facts
   are **measured** (ADR 0045): the platform ran the code that produced them. The project's own
   measured facts (`dod.facts`) win over the job's on a name clash.
6. **Traced, not trusted on its word**: `job.started` records the argv and image; `job.finished` the
   exit code, the duration and the tails of stdout and stderr, with the run token and any recognised
   secret masked; the result's reports carry the image and the argv's sha256.

## Consequences

- A deterministic computation is a step like any other: it can be gated (`evidence_facts` on its
  measured facts), retried, escalated, and its artifacts are stored and hashed.
- The `ActorType.JOB` value and `JobActor` are a contract change; old histories hold no job, so the
  interpreter needs no `patched` branch.
- A job image must contain the runner (it is the runner that runs the command): job images are built
  **from** the runner image, the way S37-06 locked headroom into it.
- Reproducibility is the command's responsibility: the platform pins the image, the argv and the
  inputs; a command that reads the clock or the network without a snapshot is still not
  reproducible. The investment template reads its data through a snapshot (S38-03/S38-04) for this
  reason.

## The gates that rest on it (S38-05, S38-07)

- **`output_schema`**: `{schemas: {output: JSON Schema}}` validates a declared output (a JSON string
  is parsed first) with Draft 2020-12; the validator refuses a schema that is not one, or one for an
  output the transition does not declare.
- **`evidence_facts` with `measured_only: true`**: a fact the step only declared is refused
  ("declared, not measured").
- **`numbers_measured`**: every number an agent writes in its outputs must be found, within the
  rounding of the digits written, in the named sources (a stored document such as a job's
  `metrics`, or the work item's `fields`). Years and small integers are ignored; a percentage
  matches either its fraction or its written value.

## Not decided here

- A job fanning out over N items (S38-12).
