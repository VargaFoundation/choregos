# 0181 — The contracts speak no trade: neutral names for the core, software delivery as a pack, decided before the 1.0 freeze

- **Status**: proposed, 2026-11-17. The project lead decides in December 2026 between the options below
  (plan of 2026-11-16, workstreams A1 "decision" and O4 "decision"). The work is tracked by the
  `contract-change` issue [#1832](https://github.com/graalsystems/tranibor/issues/1832).
- **Concerns**: `packages/contracts` (the schemas, `openapi.yaml`, `src/choregos_contracts/`, `ts/`), the
  five surfaces of [ADR 0168](0168-the-surfaces-frozen-for-1-0.md), `packages/core/src/choregos_core/gates/`,
  `packages/core/src/choregos_core/dsl/`, `apps/orchestrator/src/choregos_orchestrator/activities/gates.py`,
  `packages/playbooks`, `packages/ontology`, `packages/quant`. Follows
  [ADR 0001](0001-frozen-contracts.md) (a contract moves by a `contract-change` PR),
  [ADR 0029](0029-a-platform-for-governed-agents.md) (a platform for any trade),
  [ADR 0098](0098-english-everywhere-finished.md) (no alias left; dual read of persisted values),
  [ADR 0133](0133-a-plugin-ships-an-assistant-an-organisation-installs-it-for-an-audience.md) point 7 (the
  core names no business) and [ADR 0168](0168-the-surfaces-frozen-for-1-0.md). It is written to line up with
  [ADR 0176](0176-the-core-is-named-tranibor.md) (the Tranibor rename rounds).

## Context

ADR 0029 made the platform general-purpose, but the generalisation happened in the mechanics only. The
contracts still use software-delivery vocabulary. On 1.0, ADR 0168 freezes five surfaces: the REST contract,
the MCP door, the A2A door, the trigger route and the enterprise seams. After that point a name cannot be
renamed in place. A rename adds a new surface beside the old one and serves both for at least 90 days
(ADR 0168 §2). So any name still there on 1.0 stays for the whole major version. The plan of 2026-11-16 puts
1.0 on 2027-06-30. ADR 0168 itself states a target of 2027-04-30.

The plan's critique (#7) says this: "the contracts keep the software vocabulary (`fix_ci`, `release_captain`,
`pr_url`); 12 gates out of 22 depend on a diff or a CI". It cites `specs.py:695-720`. Checked against
`main` on 2026-11-17:

- **The 12 out of 22 figure is right.** The gate registry (`choregos_core/gates/__init__.py`) has 22 gates.
  Twelve of them declare `needs=("scm",)`.
- **The three names exist.** `fix_ci` and `release_captain` are in `choregos_contracts/enums.py`.
  `pr_url` is in `stage.py:460` and in the `WorkItem` schema of `openapi.yaml`.
- **The citation points at something else.** `packages/adapters/src/choregos_adapters/specs.py:695-720` is
  the three demonstration business families (`mdm`, `shipping` and `access_control`, all `demo` and
  `dev_only`). Those lines support the critique's other claim, that the HR model rests on connectors that
  exist only as demonstrations. They have nothing to do with the vocabulary.

Two places are worse than the critique says:

- **The core interpreter hard-codes two software roles.** `WorkflowEngine.select_transition`
  (`choregos_core/dsl/engine.py:110-116`) maps the outcome `ci_failed` to an agent of role `fix_ci`, and
  `changes_requested` to an agent of role `address_review`. It skips those roles on the normal path. The
  Temporal interpreter (`workflows/interpreter.py`) calls this function, so the mapping is part of the
  workflow's deterministic behaviour.
- **Requirements are inferred from role names.** `choregos_core/dsl/requirements.py:34` infers that a
  project needs an `scm` from the role names `implement`, `fix_ci`, `address_review`, `review`,
  `release_notes`, `architect` and `document`.

The repository already has the guard pattern this needs. `apps/api/tests/test_core_names_no_business_vocabulary.py`
(S63-19, ADR 0133 point 7) rejects investment vocabulary anywhere in the core. Its allowlist is checked in
both directions. It does not cover software delivery, though: software delivery was the first use, and the
guard was written against the second one.

### What "the five surfaces" carry

The REST contract embeds `project.schema.json` (`openapi.yaml:7130`), `workflow.schema.json` (`:7822`,
`:7991`), `policy.schema.json` (`:10163`), `stage-result.schema.json` (`:12309`) and `template.schema.json`
(`:12958`). A REST guard that compares request and response schemas therefore freezes the workflow DSL,
the policy, the project manifest and the stage result too. ADR 0168 left one thing open: which operations
are in the frozen set. This record proposes the answer for the trade-specific ones (point 5 below).
`stage-input.schema.json` is the orchestrator-to-runner contract. It is not reachable through any of the
five surfaces, so ADR 0001 is the only rule it follows.

## Inventory

Each name gets one of three classes:

- **(a)** already generic;
- **(b)** software-specific, but legitimately the concern of a software-delivery plugin, so it moves out of
  the core;
- **(c)** a concept the core owns under a software name, so it needs a neutral name.

### Python types (`packages/contracts/src/choregos_contracts/`)

| Name | Where | Class | Proposal |
| :-- | :-- | :-- | :-- |
| `StageRole`: `triage`, `refine`, `plan`, `implement`, `verify`, `review`, `custom` | `enums.py:23-35` | (a) | Stays. `implement` no longer implies an `scm` (`requirements.py:34`). The pack declares which of its roles need one. |
| `StageRole`: `fix_ci`, `address_review`, `release_notes`, `verify_prod` | `enums.py:30-34` | (b) | Declared by the pack. The workflow schema's `role` is already a free pattern (`workflow.schema.json:192-196`), so only the Python enum and the description change. `playbooks.KNOWN_ROLES` also lists `brief` and `deliver`, which `StageRole` lacks. The two lists are aligned in the same round. |
| `Role.DEVELOPER`, `Role.RELEASE_CAPTAIN` | `enums.py:138-143`, `workflow.py:303` | (c) | Become `contributor` and `approver`. `org_admin`, `project_owner` and `viewer` stay. |
| `ConnectorKind`: `scm`, `ci`, `cd` | `enums.py:127-135` | (b) | The pack declares them. OpenAPI already describes `kind` as open: "or one a plugin type declares" (`openapi.yaml:7199`). |
| `ConnectorKind`: `tracker`, `runtime`, `memory`, `notify`, `gateway` | `enums.py` | (a) | Stay. |
| `InboundEventType`: `scm.pr.*` (5), `scm.check.completed`, `ci.run.*` (3), `cd.*` (4) | `enums.py:280-302`, `inbound-event.schema.json` | (b) | The schema's `enum` becomes a `pattern` plus the core list, and the pack declares its types. The strings do not change: they are stored in `received_events`. |
| `InboundEventType`: `tracker.item.*`, `alert.*`, `human.decision` | same | (a) | Stay. |
| `EventType.RELEASE_*` (8), `ReleaseStatus` | `enums.py`, `event.schema.json` | (b) | Belong to the release train (see below). Event type strings are wire values and are never renamed (ADR 0176 §6 applies to them in the same way). |
| `FindingType`: `bug`, `perf`, `tech-debt`, `flaky-test` | `enums.py:83-91`, `finding.schema.json:11` | (b) | The enum opens to declared types. The pack declares these four, the core keeps `security`, `docs` and `ux`, and all seven values stay valid. |
| `MemoryKind`: `flaky_test`, `hotspot` | `enums.py:155-166`, `context-pack.schema.json:46` | (b) | Same treatment as `FindingType`. |
| `ExecutorKind.TEKTON` | `enums.py` | (a) | A runtime backend, not a trade. |
| `ActionOrigin.ONTOLOGY` | `enums.py:271-276`, `openapi.yaml:7719` | O4 | See [O4](#o4-the-quant-and-ontology-packages). |

### Workflow DSL (`workflow.schema.json`, reachable through REST)

| Name | Where | Class | Proposal |
| :-- | :-- | :-- | :-- |
| Approver `role` enum `developer`, `release_captain` | `:691-705` | (c) | `contributor`, `approver` (with the same round as `Role`). |
| `does: open_pr \| merge_pr`, `pull_request.draft` | `:537-556` | (b) | `does` becomes a name resolved by an **effect registry**. The pack registers `open_pr` and `merge_pr` under those same names, so stored workflows keep validating. |
| `via: release_train`, `train` | `:399-419`, `oneOf` at `:562-585` | (b) | The pack's. |
| Repair by role (`fix_ci`, `address_review`) | `engine.py:110-116` (behaviour, not schema) | (c) | A transition declares `repairs: <outcome>`, and the interpreter picks it on that outcome. The pack's workflows declare `repairs: ci_failed` and `repairs: changes_requested`. Behind a `workflow.patched` id, with a replayed history (ADR 0008). |
| `on_changes_requested`, `review` | `:489-520` | (a) | Any human or agent review can request changes. |
| Gate names | `gateRef`, `:608-631` | (a) as a schema | Free strings. Their meaning comes from the registry (see [Gates](#gates)). |
| `output_types`: `diff` | `:476` | (a) | A diff of any text. |

### Project, policy and stage contracts

| Name | Where | Class | Proposal |
| :-- | :-- | :-- | :-- |
| `repo`, `repositories` (`url`, `default_branch`, `branch_prefix`, `language`, `lint_command`, `test_command`, `typecheck_command`, `clone_depth`), `gitops` | `project.schema.json` | (b) | A `delivery` section that the pack owns and validates. The core keeps a neutral `workspace` notion: something a step reads and writes, with git as one implementation. |
| `scope.max_diff_files`, `scope.max_diff_lines`, `approvals.merge`, `release_train` | `policy.schema.json` | (b) | The pack's policy section. `scope.deny_paths` and `approvals.{spec,prod,scope_change}` stay (a). |
| `repo` (`url`, `base_branch`, `work_branch`, `clone_depth`), `project.{lint,test,typecheck}_command`, `work_item.links.pr` | `stage-input.schema.json` | (b) | A `delivery` block. `links.pr` becomes `links.change_request`. Internal contract: ADR 0001 only. |
| `artifacts.branch`, `artifacts.commits`, `artifacts.pr_url` | `stage-result.schema.json:98-120`, `stage.py:460` | (b), and (c) for `pr_url` | `pr_url` becomes a generic `links: [{rel, url}]`, with the pack's `rel: change_request`. Branch and commits go to the `delivery` block. |
| `evidence.diff_files`, `diff_lines`, `tests_run`, `tests_passed`, `tests_failed`, `lint`, `typecheck`, `coverage_delta`, `security_scan` | `stage-result.schema.json` | (b) | Become named measured facts in the existing `evidence.facts` / `evidence.measured` (ADR 0056), which are already trade-neutral. The runner writes both forms for one window. |
| `outputs.release_notes_markdown` | `stage-result.schema.json` | (b) | The pack's output. `plan_markdown`, `spec_markdown` and `review_markdown` stay (a). |

### REST (`openapi.yaml`, 477 operations)

33 operations name software delivery. Classified:

| Operations | Class | Proposal |
| :-- | :-- | :-- |
| The `trains` tag: `listReleases`, `getRelease`, `getTrainStatus`, `departTrain`, `freezeTrain`, `unfreezeTrain`, `approveRelease`, `abortRelease` | (b) | The pack's operations, outside the core's frozen set (point 5). |
| `githubWebhook`, `gitlabWebhook`, `tektonWebhook`, `argocdWebhook` | (b) | Connector routes, outside the frozen set. |
| `getGitHubAppStatus`, `refreshGitHubInstallations`, `listGitHubRepositories`, `createGitHubInstallLink`, `githubAppSetup`, `getPlatformGitHubApp`, `forgetPlatformGitHubApp`, `startGitHubAppManifest`, `githubAppManifestCallback` | (b) | Connector routes, outside the frozen set. |
| `getRunDiff`, `getProjectDora` | (b) | The pack's. |
| `getRunCiLogs`, `postRunGitToken`, `postRunFinding` (`/internal/*`) | (b) / (a) | Internal routes that 1.0 does not freeze. |
| `previewSkillsFromGit`, `importSkillsFromGit` | (a) | Git is a transport for skills, not a trade. |
| The `agents` tag's `*Release*` operations | (a) | The release of an agent's version. |
| `listFindings`, `postFindingAction` | (a) | The type list is (b), as above. |

Component fields:

| Field | Class | Proposal |
| :-- | :-- | :-- |
| `WorkItem.pr_url` | (c) | `links`. |
| `WorkItem.repository`, `WorkItem.repository_fixed` | (c) | `workspace`, `workspace_fixed`. |
| `ProcessStep.writes_branch` | (c) | `writes_workspace`. |
| `WorkflowCatalogueEntry.needs_repository` | (c) | `needs_workspace`. |
| `ArtifactFlow.in_pull_request` | (c) | `in_change_request`. |
| `ProjectStats.first_pass_merge_rate`, `MemoryAbGroup.first_pass_merge_rate` | (c) | `first_pass_acceptance_rate`: the share of work items accepted without a rework loop. |
| `Project.has_release_train`, `ProjectStats.trains_pending`, `TrainStatus.current_release`, `DoraReport` | (b) | The pack's. |
| The membership role enums (`openapi.yaml:6913`, `:7677`) | (c) | `contributor`, `approver`. |
| The provider enums `[github, gitlab, forgejo]` (`:3807`, `:6950-6976`, linked accounts, ADR 0161) | (a) | These are identity providers a member links. They name systems, not a trade. |

### The other four surfaces

- **MCP door.** The eleven tools (`apps/api/src/choregos_api/mcp/tools.py:720-980`; `docs_search`, `docs_read`,
  `list_projects`, `describe_workflow`, `create_work_item`, `search_work_items`, `get_work_item`,
  `summarize_run`, `list_pending_decisions`, `get_pending_decision`, `draft_decision`) and their input
  schemas are (a). Their structured outputs carry no software field either: `get_work_item` returns the
  state, the pending decision, the cost and the timeline, not `pr_url` (`mcp/tools.py:371-387`). The MCP
  profile of ADR 0169 can therefore freeze them as they are.
- **A2A door** (`a2a/door.py`, `a2a/v1.py`, the `skill-fields/v1` extension): (a). It names no trade.
- **Trigger route** (`/webhooks/triggers/{org}/{project}/{name}`, ADR 0113): (a).
- **Enterprise seams** (`edition.declare*`, `extensions.declare_*`, `tenant_sign_in.declare_sign_in_resolver`):
  (a). The entry-point groups `choregos.plugins` and `choregos.migrations` are product names, not trade
  names, so they belong to ADR 0176 R2.

## Gates

There are 22 gates in `choregos_core/gates/__init__.py`, and the workflow schema does not enumerate them.

| Group | Gates | Reads |
| :-- | :-- | :-- |
| Diff (`DIFF_GATES`) | `scope_respected`, `diff_size_max`, `no_secrets`, `markdown_sections`, `docs_updated`, `adr_number_free` | the work item's diff on the SCM |
| Repository or CI | `coverage_delta_min`, `ci_green`, `review_approved`, `scans_ok`, `provenance_signed`, `already_fixed_proven` | the runner's measurement in a clone, or the pull request's checks and review |
| Trade-neutral | `evidence_present`, `outputs_in`, `outputs_present`, `output_schema`, `evidence_facts`, `numbers_measured`, `tool_called`, `action_succeeded`, `external`, `flag_present` | the stage result, the item's documents and fields, the cost ledger, the action's state, an external URL, a flag provider |

The first two groups are the twelve that declare `needs=("scm",)`. `flag_present` reads a feature-flag
provider and does not need the SCM. Its name is generic, though its message says "the code".

**Proposal: the twelve become gates provided by the pack, without changing their names.** Gate names are
written in stored workflow versions, so a rename would be a DSL break with no gain. What moves is
ownership:

1. **A gate seam (round C0, no break).** A plugin registers gates through the existing `gate()` decorator,
   loaded from its `choregos.plugins` entry point. It also declares a **context provider**: an
   activity-side function that fills the facts its gates read. `GateContext` keeps only the neutral fields
   (`result`, `expected_outputs`, `tool_calls`, `action_status`, `documents`, `fields`, `parent_documents`,
   `external_results`, `flags`) plus a `facts: dict[str, Mapping]` keyed by provider. The diff fields
   (`changed_files`, `additions`, `deletions`, `allowed_paths`, `denied_paths`, `secrets_found`,
   `added_text`, `default_branch_files`, `diff_available`) and the CI fields (`ci_status`, `review_state`,
   `scans`, `signed`, `branch_pushed`) become the pack's provider facts. The constants `DIFF_GATES`,
   `RUN_SCOPED_GATES` and `OVER_WORK_ITEM` move with it.
2. **The twelve move (round C2).** They move into a first-party package of this repository, the
   software-delivery pack, registered through that seam. The activity names in
   `apps/orchestrator/src/choregos_orchestrator/activities/gates.py` do not change, so no history needs a
   patch.
3. **A workflow that names a gate whose provider is not installed is refused at publication.** The refusal
   names the pack. Today an unknown gate is a configuration error at evaluation time
   (`GateError: unknown gate`).

## Decision (proposed)

1. **The core's own concepts get neutral names before 1.0, and software delivery becomes a first-party pack.**
   The pack is a package of this repository registered through the plugin seam, and the core's
   contracts and frozen surfaces do not name it. This is option **C** in the cost table. Options A, B and D
   are what the project lead may choose instead.
2. **Three contract-change rounds, each its own PR and release, each with its own guard.**
   - **C0 (no break; January 2027).** A vocabulary guard, modelled on
     `test_core_names_no_business_vocabulary.py`. It covers `packages/contracts` and the five surfaces,
     takes the software terms of the inventory above, and has a both-ways allowlist that starts with
     today's hits. It is a ratchet: entries leave and none are added. The round also adds the gate seam
     and context providers, the effect registry for `does`, and the open lists for inbound event types,
     finding types, memory kinds, connector kinds and stage roles. Nothing is renamed.
   - **C1 (neutral names; the same minor as ADR 0176 R2).** `developer` becomes `contributor` and
     `release_captain` becomes `approver`. `pr_url` becomes `links`. The `repository` fields of the REST
     components become `workspace`. `in_pull_request`, `writes_branch`, `needs_repository` and
     `first_pass_merge_rate` get the names above. Repair by role becomes `repairs:`, behind a
     `workflow.patched` id with a replay. The OpenAPI title becomes "Tranibor API", which ADR 0176 §6
     leaves to a contract-change PR.
   - **C2 (the pack; February to May 2027).** The twelve gates, `does: open_pr | merge_pr`, the
     `delivery` sections of the project, policy, stage-input and stage-result contracts, the inbound
     `scm.*`, `ci.*` and `cd.*` types, the release train's contract and the DORA and train statistics
     move to the pack. The release train's code stays where it is until after 1.0, under the pack's name.
3. **There is no alias policy, because ADR 0098 removed aliases. Each round has one window instead, and
   the removal comes in its own PR.** Concretely:
   - **Python names** get no alias of their own. C1 lands in the minor where R2 renames
     `choregos_contracts` to `tranibor_contracts`. The new package starts with the neutral names, and the
     `choregos_contracts` shim of R2, kept for one minor, also exports the old ones. The enterprise edition
     and the plugins change each import once, and R2's shim removal also removes the old names.
   - **REST, MCP and DSL fields and values** are added beside the old ones in round N, then removed in
     N + 1 (one minor). Responses carry both, and requests accept either. Before 1.0 this is a
     `contract-change` PR (ADR 0001). After 1.0 it would be a new surface served for 90 days
     (ADR 0168 §2), which is why the rounds must finish before the freeze.
   - **Persisted and recorded values** follow ADR 0098 §4: readers accept both spellings from round N, and
     writers switch in N + 1. Recorded Temporal histories are never rewritten (`verify_prod` appears in
     five of them, all `tests/replay/histories/train-*.json`). Stored workflow versions are never rewritten
     either, because their digest would move: a stored version with the old role spelling keeps
     validating, and publishing a new version with it is refused after the window. `memberships.role` is
     platform-owned state: an Alembic data migration rewrites it in the removal release, once the
     enterprise group mapper (`edition.declare_group_mapper`) emits the new names.
   - **Event type strings, inbound event type strings and gate names do not change.** Subscribers, stored
     rows and stored workflows key on them, so they change owner and keep their name.
   - Every name kept for a window goes on **one list, with the release that removes it**. A test fails
     when that release is reached and the name is still there. The list is empty at 1.0, so the rule
     "there is no alias any more" holds on the frozen surfaces.
4. **The Tranibor rounds and these rounds are sequenced, so that nothing is renamed twice:**
   - C1 ships in the same minor as **R2**, as described above.
   - The **O4** removal (`ontology`) and move (`quant`) come **before R2**, or R2 skips those two packages.
     Either way, R2 writes no shim for a package that is leaving.
   - **R3** lists an image `tranibor-quant` (ADR 0176 point 4). No such image exists: `choregos-quant` is
     a console command installed in the runner image (`docker/runner.Dockerfile:249-254`). If quant
     leaves first, R3 has one less name to carry. The correction of ADR 0176 is a follow-up of its own
     stream (see Consequences).
   - **R4** does not alias `CHOREGOS_ONTOLOGY_TRIAL` if ontology has already gone.
   - Wire names that ADR 0176 §6 keeps (headers, cookies, `choregos/v1`, the `choregos.*` event types) are
     not touched by either rename.
5. **The frozen REST set excludes the pack's and the connectors' operations.** The REST guard of ADR 0168
   §3 carries the list of frozen operations. The operations classed (b) above are not on it. They follow
   the pack's own versioning, as the plugin server protocol does under ADR 0168 §1.

## O4: the `quant` and `ontology` packages

| | `packages/ontology` | `packages/quant` |
| :-- | :-- | :-- |
| Size | 21 source files, 4,192 lines (3 migrations; `service/` is 1,848 lines); 13 test files, 2,468 lines, plus 1,930 lines of snapshots | 13 source files, 1,372 lines, plus `schemas/strategies.schema.json`; 7 test files, 957 lines; no dependencies |
| Activation | Off by default. `CHOREGOS_ONTOLOGY_TRIAL` (`service/plugin.py:26`): while it is unset, `register()` returns early and the migrations point to an empty directory. It is on only in `trials/local-start/compose.yaml:28`. | No flag. It is a console command in the runner image, called by a deterministic job step (ADR 0056). |
| Who imports it | Nothing in the core's `src` imports it. It plugs in through `choregos.plugins` and `choregos.migrations`. Tests: `apps/api/tests/test_mcp_docs.py:424`, `tests/conformance/templates/test_templates.py:237-239`, `apps/api/tests/test_errors_in_english.py:32`. `tests/architecture/test_packages_imports_of_the_api.py:10` allows its `service/` to import the API. | Nothing imports it. Dependencies: `packages/runner/pyproject.toml:26-28`, `docker/runner.Dockerfile:249-254`. Tests: `tests/packaging/test_quant_in_the_runner_image.py`, `tests/e2e/test_m7_investment.py`, `tests/conformance/templates/test_investment_research_*`. |
| Contract and state | `ActionOrigin.ONTOLOGY` (`openapi.yaml:7719`, `ts/api.ts`), `template.schema.json:154` (`defaults.extensions.ontology`). Persisted: `origin='ontology'`, the effect kinds `ontology.effet` and `ontology.preuve`, `params.ontologie` (frozen by ADR 0098 §4). Its own Alembic branch (`onto0001` to `onto0003`: `ontology_versions`, `managed_objects`). | None. The investment template's snapshot job stores `bars` artefacts that a relaunched campaign reads (S38-16). |
| Users | The `joiners-leavers` template (`manifest.yaml:37-41`), the console's `components/actions/ontology.tsx`, and the API's extension seam (`extensions.py:166,253`, MCP tools served to people `mcp/tools.py:950,993`). | The `investment-research` template (`workflows/campaign.yaml:36-41`, two agents, two skills, evals). |
| Documentation | ADRs 0029, 0030, 0035, 0036, 0039, 0085, 0098, 0099, 0150, 0176; `docs/deployment.md`, `docs/development.md`, `docs/getting-started.md`, `docs/usage.md`, `trials/ontology/` | ADRs 0056, 0176; `docs/usage.md`, `docs/concepts.md`, `docs/code-agents.md`, the two `connector.marketdata.*` procedures, `trials/investment/` |

Options for December:

- **O4-1: remove `ontology`.** Delete the package and its tests and the `ontology:` entry of the
  `joiners-leavers` template and its fixture. A core migration drops `ontology_versions` and
  `managed_objects` `IF EXISTS`. The trial was switched on only in `trials/local-start`, but an
  installation that enabled it holds those tables. The console's ontology component is removed. Readers
  keep accepting the persisted `origin='ontology'` and the frozen effect kinds, and the contract's `origin`
  enum keeps `ontology` as a value that is read and never written. The extension seam it used stays,
  because it is generic.
- **O4-2: keep `ontology` outside the core.** It already plugs in through entry points, so it moves to its
  own distribution or plugin repository and leaves R2's list.
- **O4-3: move `quant` to a plugin.** The CLI, the `investment-research` template and their tests become a
  plugin that ships its own job image (allowed by `CHOREGOS_JOB_IMAGES`). The runner drops the dependency.
  The six `wi-campaign-*-S38-*` replay histories never name quant and stay as tests of the generic job step.
- **O4-4: move the `marketdata` connector family as well.** The plan's indicator for O4 is "the core names
  no trade, without exemption". Today `test_core_names_no_business_vocabulary.py` exempts the `marketdata`
  connectors (`packages/adapters/src/choregos_adapters/marketdata/`, 1,107 lines with the fake, plus
  entries in `specs.py`, `families.py` and `builtins.py`). Without O4-4, the exemption list cannot reach
  zero.
- **O4-0: keep both as they are.** The exemptions stay, and R2 renames thirteen packages instead of eleven.

## Cost, in person-days

The estimates are made from the file counts of 2026-11-17. For example, `pr_url` appears in 31 source
files, `release_train` in 48, `open_pr` in 28 and `release_captain` in 14. A range covers one implementer,
the review, the removal PR, the documentation and STATUS.

| Option | Content | Person-days |
| :-- | :-- | :-- |
| **A**: freeze as is | The glossary says which names are software delivery, and the frozen REST list includes them | 1–2 |
| **B**: neutral names only | C0's guard (1–2); roles (4–6); `repairs:` with patch and replay (3–5); `links` and `workspace` (3–5); open lists (2–3); removal PR (2–3) | 15–24 |
| **C**: B plus the pack at the contract level (proposed) | B; gate seam and context providers (5–8); effect registry (2–3); twelve gates moved (3–5); `delivery` sections with dual read (6–9); train contract and statistics reclassified (3–5); frozen REST list (1) | 35–55 |
| **D**: C plus the train's code moved and the pack in its own distribution | C plus 12–20 | 47–75 |
| **O4-1**: remove ontology | | 4–6 |
| **O4-2**: ontology to its own distribution | | 3–5 |
| **O4-3**: quant to a plugin | | 3–5 |
| **O4-4**: marketdata to a plugin | | 4–6 |

For comparison, the plan sizes the whole of A1 (this decision, the renames, and the guards on the five
surfaces) at 55–80 person-days, and O4 at 8–11 (that is O4-1 plus O4-3, without O4-4). Option D does not
fit beside the rest of A1 before 2027-06-30. Option C fits only if C0 starts in January.

## The deadline

The rounds cannot start before the decision. Their order is fixed by ADR 0176: C1 ships in R2's minor, and
O4 lands before R2. Every window has to close before the freeze, because the old names would otherwise be
frozen too. If the decision comes after January 2027:

- either C1 misses R2, and every consumer (the enterprise edition, the plugins, the deployments' values)
  moves twice, with two shims and two removal PRs;
- or R2 waits for C1, and the Tranibor rounds slip with it.

Either way, the 35–55 person-days of option C then fall between March and May, beside the guards of
ADR 0168. That is the margin the plan does not have, so the 1.0 date of 2027-06-30 is lost. Option A is
the only one a late decision leaves open, and it freezes the software vocabulary for the whole of major
version 1.

## Consequences

- If C is chosen, a workflow for any trade reads in the core's contracts only what the core does. A
  software-delivery project installs the pack and reads exactly what it reads today, under the same gate,
  event and effect names.
- C0 creates the vocabulary guard, which turns red when a software term is added to the contracts or the
  five surfaces. Each later round shrinks its allowlist, and at 1.0 the allowlist holds nothing on a
  frozen surface.
- Two corrections fall outside this record, and each has a follow-up:
  - ADR 0176 point 4 names an image `tranibor-quant` that does not exist;
  - `StageRole` and `playbooks.KNOWN_ROLES` disagree (`brief`, `deliver`).
- Nothing in `packages/contracts` changes until the decision. Issue #1832 carries the rounds.

## What this record does not decide

- Which option is chosen. That is for the project lead, in December 2026.
- Whether the pack later leaves this repository (option D, after 1.0).
- The names of the HR, ITSM and investment families. The critique's other point, that their connectors
  exist only as demonstrations, belongs to the real-system proofs (P1), not to the vocabulary.

## Alternatives discarded

- **Renaming the gates, event types and effects too.** They are written in stored workflows, stored rows
  and third parties' subscriptions. They change owner and keep their names.
- **Renaming after 1.0.** Each rename would then be a new surface served beside the old one for 90 days
  (ADR 0168 §2), and the old name would remain for the whole major version.
- **One flag-day PR.** The same reason ADR 0176 gives: four layers (Python names, wire fields, persisted
  values, recorded histories) do not break in the same way.
