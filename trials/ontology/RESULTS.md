# Trial, elements 1 to 6 - results of 2026-10-03

**Question**: can the Choregos core carry the future foundation's ontology and action engine
**through its seams**, without a fork? Concretely, on the IT4IT slice:
- a validated and compiled YAML package (element 1);
- findings synchronised from an observations NDJSON v1 report (element 2);
- read by an agent through the generated MCP tools, with its run token (element 3);
- the agent proposes an action whose effect is `gitops.pull_request` (element 4);
- a human approves with re-authentication, and the platform opens the PR (element 5);
- the proof is the collector's re-run, the finding's key absent (element 6).

**Answer: yes, at the price of four changes to the core, all small and tested**: a seam for the
extension's tools, a filter on its migrations, file writing by the SCM adapter, and an
authentication time worthy of the name. The rest goes through the existing seams: extension
routes, migration branch, run token, the agent's tool path, adapters, audit.

## Set-up

- `packages/ontology`: the part without I/O (model, loading, validation, compilation, generated
  tools, observations, CEL evaluation); `choregos_ontology.service`: the **extension**, served by
  the core's API (extra `service`).
- The extension is declared by the package's `pyproject.toml` (`choregos.plugins` -> `register()`,
  `choregos.migrations` -> its Alembic branch), and **shipped inactive**: it only turns on with
  `CHOREGOS_ONTOLOGY_TRIAL=1` (in the chart, `global.extraEnv`). Inactive, it serves no route,
  announces no tool, and its migration branch is empty. `create_app()` loads it, and
  `python -m choregos_api.migrate` runs its branch.
- Its tables: `ontology_versions`, `managed_objects` (properties as JSON, key project + type + id) and
  `action_proposals`, **under forced RLS** on PostgreSQL, tied to the organisation through their project
  like the core's tables.
- Its routes: `PUT/GET /projects/{id}/ontology`, `POST /projects/{id}/observations`,
  `GET /projects/{id}/objects/{type}`, `GET /projects/{id}/proposals[/{p}]` and
  `POST /projects/{id}/proposals/{p}/decision`, under the core's rights.
- Its tools: through the **new seam** `declare_tool_provider`, they reach the agent through the
  catalog's path: `GET /internal/runs/{id}/tools` announces them to the runner's `choregos-tools` MCP
  server, `POST /internal/runs/{id}/tools/{name}` calls them under the run's token,
  with the per-run call ceiling, a line in the cost ledger and the `tool.called` event.
- The IT4IT slice's package (`packages/ontology/tests/fixtures/it4it`): `finding`, `host`,
  `service`, the `open_infra_pr` action (effect `gitops.pull_request`, paths `platform/**` and
  `gitops/**`, proof: the PR is open on the right branch), the `verify_finding_fixed` action (proof
  `collector.rerun`, key absence required) and the `infra_change` policy (low risk: automatic;
  otherwise an `owner`, authentication less than 10 minutes old).

## What was proved

**Elements 1 to 3**

| # | Check | Result |
|---|---|---|
| 1 | The `core-ref` package is validated, compiled, and becomes the active version; a second push replaces the first; 15 generated tools | ✅ |
| 2 | An invalid package is refused (422) with its located errors, and does not replace the active version | ✅ |
| 3 | A file name that leaves the package (`..`, absolute path, non-YAML) is refused | ✅ |
| 4 | A report opens one finding per (`layer`, `check`) with all its scopes; **its replay writes nothing**, not even a row version | ✅ |
| 5 | **A partial report writes nothing**: no `_end` line, wrong count, invalid line - even when it would have resolved and opened findings | ✅ |
| 6 | Only `ok` on the same scope resolves; `unreachable` and an absent layer resolve nothing; a contradiction within a report keeps the scope open | ✅ |
| 7 | A reader (`viewer`) reads the objects but does not synchronise (403) | ✅ |
| 8 | The run sees the tools the trial can serve, and only those: neither `alert_*` (datasource `connector`), nor an action whose effect is not served | ✅ |
| 9 | The agent reads the findings (`finding_search`), a host (`host_get`), its services and a service's owner (links); **the platform's journal** (`tool.called`) carries these calls, in order | ✅ |
| 10 | A `confidential` property (`ip`) is absent from the results **and from the filters** | ✅ |
| 11 | An argument outside the schema is refused (400); without a token, 401; with another run's token, 403 | ✅ |
| 12 | Another project's run only sees its own objects | ✅ |
| 13 | **Through the agent's real MCP server** (`choregos-tools`): `tools/list` returns its tools and the ontology's, `tools/call host_get` returns the host without its address | ✅ |
| 14 | The migration branch creates the tables after the core, and steps down by itself | ✅ |
| 15 | On PostgreSQL: RLS forced on the three tables; an organisation sees neither the objects nor the ontology of another; a session without an organisation sees nothing; a write into another organisation's project is refused | ✅ |

**Elements 4 to 6**

| # | Check | Result |
|---|---|---|
| 16 | The agent proposes `open_infra_pr` through its tool, with its token: **`pending_approval` proposal, with its justification**, proposed by `agent:platform` on behalf of the run; **nothing is opened** before approval | ✅ |
| 17 | The same proposal twice returns the first (idempotency key) | ✅ |
| 18 | A path outside `paths_allowed` (or going up through `..`) is refused **at the proposal**; a false precondition (resolved finding) too; nothing is recorded | ✅ |
| 19 | Approving with an authentication more than 10 minutes old: **401 towards `?reauth=1`**, no PR; after re-authentication: the platform writes the file on `choregos/<proposal>`, opens the PR, and the proof records it (`succeeded`) | ✅ |
| 20 | **The decision is in the file with its `auth_time`**, that of the session opened by `?reauth=1` | ✅ |
| 21 | A developer does not approve an action that requires an `owner` (403); a rejection closes without effect; a decision cannot be taken back (409) | ✅ |
| 22 | If the SCM returns a branch other than the expected one, the proof fails and the action is `failed` | ✅ |
| 23 | `verify_finding_fixed` (low risk, approved automatically) waits for the next report; **without the key, `succeeded`**, and the `collector_clean` fact is `true` | ✅ |
| 24 | **Key still present, layer absent, layer `unreachable`: `failed`**, never "key absent" | ✅ |
| 25 | A partial report fails the proof that was waiting for it, **without writing any object**; with no report within 15 minutes, `failed` | ✅ |
| 26 | Through the agent's real MCP server, the proposal arrives `pending_approval` | ✅ |
| 27 | A decision requires a human session: an API token is refused (403 `decision_requires_session`); a refusal requires a reason (422); separation of roles returns 422; too old an authentication returns 401 `step_up_required` (contract 03 §12, R-SOC-ACT-04) | ✅ |
| 29 | A human proposal (`POST /projects/{id}/proposals`) follows the same rules as an agent's; the right comes from the roles (`role:contributor`), a reader is refused (403), parameters outside the schema too (422); the inventory (`register_host`) is written by the action engine | ✅ |
| 30 | An action the agent has no right to propose is not in its tool list, and calling it answers like a non-existent tool (404) | ✅ |
| 31 | Without `CHOREGOS_ONTOLOGY_TRIAL`, the extension is inert: no route, no tool, no table | ✅ |
| 28 | **Against the running integrated stack** (PostgreSQL under RLS, uvicorn, Temporal): elements 1 to 6 end to end - see `trials/local-start/RESULTS.md` | ✅ |

To replay: `uv run pytest packages/ontology packages/adapters/tests/test_github_scm.py`, plus the
API tests of extension tools and of fresh authentication
(the PostgreSQL tests need `CHOREGOS_TEST_DATABASE_URL`). Each guard was removed once
to check that a test goes red: tool collision, `project:write`, filtering by sensitivity,
core migrations filter, authentication freshness (engine and core), `paths_allowed`,
approver's role, preconditions, absent layer, `unreachable` layer.

## What the trial had to change in the core

1. **A seam: the extension's tools** (`extensions.declare_tool_provider` and
   `routers/internal.py`). Without it, an extension could serve routes, but no tool reached
   the agent: the catalog could only be read from a deployment file. A tool name already taken
   (by the catalog or another extension) is **refused with 409**, not masked.
2. **The core's migrations only describe its tables** (`db.models.is_core_object`). An extension
   registers its models in the core's `Base` - the enterprise edition does the same -: without this filter,
   `test_migrations.py` goes red as soon as an extension was imported earlier in the test session
   (observed), and an autogeneration run with an extension installed would put its tables into a
   core migration. **To carry over to `choregos-ee`.**
3. **The SCM adapter can write files** (`ScmAdapter.commit_files`, GitHub through the Contents
   API, and the fake). Without it, only the PR of an agent that pushes by itself was possible; the
   `gitops.pull_request` effect requires the platform to write, token kept. Idempotent: an identical file
   is not rewritten, a PR already open on the branch is reused (the fake now does it like
   GitHub).
4. **The authentication time is the IdP's** (`auth_time` of the ID token, required recent after
   `?reauth=1`). `authenticated_at` used to read the session's `iat`: a silent SSO reconnection
   produced a fresh time without any authentication, and every "recent authentication" gate -
   that of element 5, the enterprise edition's freshness check - could be
   bypassed. **Issue #153** for the main line.

## What the trial does not prove

- **That a model picks these tools**: no LLM in the loop. The path agent -> `choregos-tools`
  -> internal API -> extension is real; the choice of calls is written by the test.
- **The console**: the decision goes through the API, not through a screen.
- **A real GitHub**: the PR is opened on the core's fake; writing through the Contents API has its
  adapter tests, no trial against a real repository.
- **Crash recovery**: the engine runs in the request, not in `ApprovalWorkflow` and
  `ActionWorkflow` (Temporal). A crash between opening the PR and the proof leaves the proposal
  `running`. *Lifted on 2026-10-06 (S20-08)*: a proposal is a core action, decided by
  `decide` and played by the `ActionWorkflow` - effects and proofs under their key, `collector.rerun`
  waiting for its proof (`awaiting_evidence`) until the next report or the deadline; `onto0003` copied
  `action_proposals` into `actions`, then removed it.
- **The collector's trigger**: the `collector.rerun` proof waits for the next report, but
  the trial triggers nothing (no connector framework); the report is posted by the test.
- **Synchronisation by the action engine**: it is a route, not the `connector.sync` system action
  under standing authorisation (R-SOC-CON-04). The `observations_partial_read` fact
  is not published (422 only): publishing an event requires a type in the contract, hence a
  `contract-change` PR. Decisions and state changes go to the core's audit journal.
- **Query scaling**: the filters are evaluated in Python on the objects of the type;
  the spec asks for expression indexes (R-SOC-ONT-16).
- **The two-step plan and apply of an ontology version** (SOC-010).
- **Hosts and services** are put in the database by the test: only the synchronisation of findings
  exists.

## Differences from the requirements document

- `managed_objects` without an `org_id` column: RLS derives it from the project, as the core does. To be settled
  in the spec (10 §1.2).
- Only two version statuses (`active`, `superseded`) instead of seven.
- An extension's tools cost nothing in the ledger (`cost_eur = 0`): the spec does not say whether an
  object read has a price.
- Every run proposes under the `agent:platform` identity: agent identities (R-SOC-MCP-03)
  do not exist.
- The verification action needs an effect (`effects` requires at least one): `verify_finding_fixed`
  dates the request on the finding (`verification_requested_at`). The spec should say whether an action
  without an effect, reduced to its proof, is allowed.
- The core's model does not distinguish a PR closed without a merge (`PrState` only has `merged`): the
  proof returns `open` or `merged`.
- `open_infra_pr` is declared `reversible` and not `compensable`: the spec's "close the PR"
  compensation (AGT-014) asks for an effect the trial does not serve.

## Effort

Measured in volume, not in time (the work was done by an agent, without timing):
- elements 2 and 3: ~980 lines of extension code, ~980 lines of tests, **115 lines added to
  the core**;
- elements 4 to 6: ~1,000 lines of extension code (including the action engine and CEL evaluation),
  ~460 lines of tests, the IT4IT package, and **110 lines added to the core** (SCM writing,
  authentication time), with their tests.

The D0 decision report (TRV-010) will have to relate this volume to the estimate of the
corresponding stories.
