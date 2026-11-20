# 0180 — One quarter of depth: the scope is frozen

- **Status**: accepted, 2026-11-17 (decided by the project lead; written by story S80-01). §1 and the
  *frozen* verdicts of §2 lifted on 2026-11-19 by [ADR 0192](0192-the-scope-freeze-is-lifted.md); §3 and §4 stand.
- **Concerns**: every stream of `docs/plan/08-backlog.yaml` and every status row (`docs/plan/status/<stream>.md`, indexed by `docs/plan/STATUS.md`);
  the roadmap [PLAN-2026-11-17-engine-to-product](../plan/PLAN-2026-11-17-engine-to-product.md) and
  the triage of partial stories [TRIAGE-2026-11-17](../plan/TRIAGE-2026-11-17.md); `CODEOWNERS` and
  `.github/PULL_REQUEST_TEMPLATE.md` (human review)

## Context

In eight weeks the repository opened more than seventy streams: HR, ITSM, investment research, hosted
agents and voice, outposts, assistants, a native CLI, an editor extension, A2A, SAML, plugins. On
2026-11-17 the status table carries 329 rows marked 🟡 (partially delivered), against 771 ✅. The
gaps those rows name are mostly the same few: the story was proved against fakes and never against
the real system, the console was tested against its mock API, a cluster run is missing.

A read of the code on 2026-11-16 found that the guarantees the platform states are not yet all
mechanisms. Verified again on the morning of 2026-11-17, before the first S80 stories merged:

- `CHOREGOS_FAKES` (`Settings.fakes`, `apps/api/src/tranibor_api/config.py`) was refused nowhere in
  production (S80-03 refuses it the same day).
- The agent runs in the runner's container by default: `runner.sandbox.agentIsolation: process` in
  `charts/choregos/values.yaml` (ADR 0146; `container` exists for `agent_sandbox` only).
- The audit log is a plain insert (`apps/api/src/tranibor_api/audit.py`, 44 lines): no chaining, no
  append-only table, no export.
- Temporal is reached without TLS and without a payload codec: `Client.connect(address, namespace=…)`
  in `apps/orchestrator/src/tranibor_orchestrator/worker.py`, `train_client.py` and
  `apps/api/src/tranibor_api/temporal.py` (S80-06 added TLS and a payload codec the same day, off by
  default, ADR 0183).
- None of the 134 `uses:` lines of `.github/workflows/` was pinned by commit SHA, most base images
  were referenced by tag, and `docker/runner.Dockerfile` installed `npm@latest pnpm@latest` (S80-04
  pins them the same day).
- The only supported install is Kubernetes; `compose.yaml` says it is "for a laptop".
- `CODEOWNERS` names nine teams that do not exist in the organisation (GitHub reports each of its 13 lines as an
  unknown owner), and branch protection on `main` requires the `ci-ok` check and no review. A change to
  a contract, a migration or a security guard is reviewed by no human other than its author.

Every PR that opens a new surface moves what the next quarter has to stabilise. The project lead's
decision of 2026-11-17 is to spend the next quarter on depth: security by default, proof against real
systems, partial stories settled.

## Decision

### 1. No new stream after 2026-11-20, other than S80

From 2026-11-20 no stream is added to `docs/plan/08-backlog.yaml` except **S80** ("From engine to
product: the plan of 2026-11-16"), which carries the roadmap's work. A story may still be added to a
stream that this record lets finish, and to S80. `tests/docs/test_scope_freeze.py` lists the streams
that exist on 2026-11-17 (plus S76, open in review that day, and S80) and fails on any other.

The freeze holds until 2027-02-28. It is reviewed at gate J2 (2027-03-12) of the roadmap; lifting,
extending or narrowing it is an amendment of this record by the project lead.

### 2. Each stream in flight is finished or frozen by name

A stream is *in flight* when, on 2026-11-17, it has a pull request open, a commit in the last seven
days, or a story not started or partial. Each one is listed here; nothing in flight is left without a
verdict.

| Stream | Verdict | What that means |
| :-- | :-- | :-- |
| S65-04, the runner and the agent in separate containers (ADR 0146) | **finish** | Continues as roadmap workstream S1: the separate container becomes the default, proved under gVisor. |
| S64, activity and tokens (ADRs 0141, 0142) | **finish** | Its partial rows close; the assistant halves (S64-09) are frozen with S63. |
| S67, the terminal (ADR 0154) | **finish** | Its partial rows and S67-03 close; nothing else is opened, except the workspace redesign and the live view of [ADR 0187](0187-the-workspace-is-one-screen-and-the-flow-is-live.md) (S67-18 to S67-29, amendment of 2026-11-18). |
| S58, several repositories (ADR 0124) | **finish** | All its stories are ✅ on 2026-11-17: nothing left to do. |
| S62, the CLI and the A2A door | **finish** | S62-07 (the weekly binary build, green on 2026-11-17) is re-read and closed. |
| S70, sign-in security in the core (ADR 0170) | **finish** | Re-authentication and revocation (S70-23) close; the SAML console screen (S70-24), the GitHub App from a manifest beyond github.com (S70-21) and the "talk to your data" plugin (S70-22, an assistant) are frozen. |
| S72, the Open Knowledge Format (ADR 0174) | **finish** | All its stories are ✅: nothing left to do. |
| S73, the publication moves | **finish** | ✅: nothing left to do. |
| S74, an organisation may own a host (ADR 0175) | **finish** | S74-03 (in review) is the stream's last story; the operator steps (issuer, TXT proof, DNS) are outside this repository. |
| S75, the core is named Tranibor (ADR 0176) | **finish** | Rounds R1 to R4 as ADR 0176 sets them: the names of the seams must settle before 1.0 (workstream A1). |
| S76, the corpus's own structure as a retrieval key | **finish** | The pull request in review (S76-01 to S76-04) is finished; no further S76 story. |
| S47, hardening from the audit of 2026-10-27 | **finish** | Its not-started security stories (S47-04, S47-05, S47-12, S47-26) are finished in the quarter; its refactors (S47-08, -09, -10, -30, -32, -33) join workstream P3; S47-25 and S47-28 are re-read by the documentation purge (A6); the others (S47-11, -13, -14, -15, -16, -21, -38) are frozen. |
| S68, the editor extension (ADR 0155) | **frozen** | S68-04 to S68-14 are not started and stay so. The extension in `apps/vscode` stays built and tested by its CI job; no feature is added. |
| S63 and S66, assistants (ADRs 0133-0140, 0158, 0159) | **frozen** | What is merged stays and is fixed when it breaks; no new assistant capability. |
| S65 outposts (ADRs 0147-0149; S65-10 to S65-36) | **frozen** | What is merged stays; no new outpost story. S65-02 (the run's API answers only where runs are), S65-05 and S65-07 (what is merged is what was judged) are not outposts and are finished through workstreams S2, P1 and A1. |
| The Tekton and Azure Container Apps executors | **frozen** | No further work on `tekton` and `aca` for agent isolation: workstream S1 isolates the agent on the Kubernetes and Docker executors only. The code stays and keeps its tests; `infra/policies/tekton` stays opt-in. |
| S41, hosted agents and voice (ADR 0080) | **frozen** | No new framework, runtime or voice story. |
| S38, investment research | **frozen** | `packages/quant` leaves the runner for a plugin in workstream O4 (decision due in December, section 4). |
| S20 HR scenario, S33 netops, S14 data platform, S50 identity on behalf of a person, S36 agent logs, S57 phase 2 (S57-12 to S57-14), S21-15 and S21-19, S61-17 (mermaid) | **frozen** | Not on the roadmap's path; they wait for a need the roadmap does not have. |
| S11, Ecphoria | **frozen here** | Its stories belong to another repository; this repository opens nothing for it. |

*Frozen* means: no new story, no feature pull request; the code stays, its tests stay green, and a
security fix or a fix to a production incident is still made. Unfreezing a stream is an amendment of
this record.

The 🟡 rows are sorted the same way, one by one, in
[TRIAGE-2026-11-17](../plan/TRIAGE-2026-11-17.md): **finish** (naming the workstream that closes the
gap), **frozen** (by name, with the reason) or **abandon** (the remainder is superseded or no longer a
deliverable). `tests/docs/test_partial_triage.py` fails when a 🟡 story is missing from it.

### 3. A human reviews contracts, security and migrations

Three areas change only after a human other than the author has read the change: the contracts
(`packages/contracts/`, ADR 0001), the migrations (`apps/api/src/tranibor_api/migrations/`,
`packages/ontology/src/tranibor_ontology/service/migrations/`) and the security guards (the paths
listed under "Human review" at the end of `CODEOWNERS`).

The mechanism is the smallest that does not stop the project's single maintainer today:

1. **`CODEOWNERS` names the paths.** A block at the end of the file (so that it wins over the
   per-stream lines) gives them to `@graalsystems/human-review`. `tests/docs/test_human_review.py`
   fails when one of those paths no longer exists, or when a later line takes it back.
2. **The pull request says who read it.** The template gains a *Human review* section: a PR that
   touches one of the areas names its human reviewer, or, while no second reviewer exists, carries
   the label `human-review-owed`.
3. **No CI gate.** Nothing blocks a merge today. The owed reviews are the list
   `gh pr list --state merged --label human-review-owed`; each is read when a second human reviewer
   joins, and its label removed.
4. **The switch is a repository setting, not code.** When a second human reviewer joins, the
   organisation creates the `human-review` team and branch protection on `main` turns on *Require
   review from Code Owners*. Gate J2 (2027-03-12) expects human review on all such changes.

### 4. Two decisions are due in December

| Decision | Owner | Prepared by | Due | Why then |
| :-- | :-- | :-- | :-- | :-- |
| The contracts speak no trade: neutral names for `fix_ci`, `release_captain` (`workflow.schema.json`), `pr_url` (`stage-result.schema.json`) and the rest of the software vocabulary in the contracts | the project lead | S80-08, ADR 0181 (proposed) | December 2026 | The renames are workstream A1's; a decision after January moves the 1.0 date. |
| The ontology is removed (`packages/ontology`), and `quant` leaves the runner for a plugin (`packages/quant`, a dependency of `packages/runner/pyproject.toml`) | the project lead | workstream O4 | December 2026 | O4 starts on 2027-02-15 with the removal; the core must name no trade before 1.0. |

## Consequences

- The roadmap's workstreams (S1 to S6, P1 to P4, A1 to A6, O2 to O4) are what the quarter delivers.
  A pull request that serves none of them, and is neither a security fix nor a fix to a production
  incident, is out of scope.
- The number of 🟡 rows becomes a measured goal: below 200 at the end of February 2027, none outside
  the stories frozen by name when workstream O3 ends (2027-04-23).
- Fewer pull requests a day, on purpose: once a second human reviewer exists, the three areas above
  wait for a reading.
- `tests/docs/test_scope_freeze.py` must be edited, citing this record, by the PR that opens a stream
  this record allows; any other stream fails it.

## Rejected alternatives

- **Freeze nothing, triage only.** The triage would be stale within a week: thirty pull requests a day
  redraw what is being stabilised.
- **A CI gate that requires an approving review now.** With one maintainer it blocks every change to
  the three areas, or teaches the habit of bypassing it with `--admin`; the gate is switched on when it
  can be satisfied.
- **Abandon every partial story whose gap is a real-system proof.** Those gaps are the roadmap's proof
  work (P1); abandoning them would erase the list of what is still unproven.

## Amendment, 2026-11-18: S67 takes the workspace redesign and the live view

The project lead, who decided this record, asked on 2026-11-18 for a cleaner and more complete terminal
workspace and for a live view of a workflow processing its items, on the web and in the terminal
([ADR 0187](0187-the-workspace-is-one-screen-and-the-flow-is-live.md)). Those stories (S67-18 to S67-30) are
added to S67, a stream this record lets finish; no stream is opened, and the row of S67 above names them.

## Amendment, 2026-11-19: the freeze is lifted

The project lead lifted §1 and the *frozen* verdicts of §2 on 2026-11-19
([ADR 0192](0192-the-scope-freeze-is-lifted.md)): streams may be opened again, the frozen streams may take
new stories, and `tests/docs/test_scope_freeze.py` is removed. The *finish* verdicts, §3 (human review)
and §4 (the decisions due in December) stand. Stream S81 is opened
([ADR 0193](0193-the-phone-is-a-surface-of-the-platform.md)).
