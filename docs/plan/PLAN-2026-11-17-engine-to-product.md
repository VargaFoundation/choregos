# From engine to product: the technical roadmap (2026-11-17 to 2027-11-30)

Stream **S80**. Decided by the project lead on 2026-11-17, with the scope freeze of
[ADR 0180](../adr/0180-one-quarter-of-depth-the-scope-is-frozen.md); the partial stories are sorted in
[TRIAGE-2026-11-17](TRIAGE-2026-11-17.md).

This page is the technical part of a plan written on 2026-11-16 from a read of the code. It keeps the
workstreams, their dates, what each waits for and when each is done, the orderings that cannot move
and the technical criteria of the six gates. The plan's other parts (the offer, the organisation, the
market) are not in this repository.

Where a line below states what the code does today, it was checked against `main` on 2026-11-17; the
plan's own figures were corrected where the code said otherwise. When this page and the code
disagree later, the code wins and the page is a bug.

Effort is in person-days for an experienced engineer working with coding agents, human review and
real-system proof included. On almost every workstream the real-system proof dominates the effort,
not the code.

## The ordering rule

A workstream goes first when it is on the path "an outside installation is installed, runs real work
and is trusted with it", or when it keeps that installation from being put at risk. Everything else
waits. Three consequences:

1. **Nothing runs real, sensitive work before security by default.** No installation outside the
   project's own takes production work before the agent is isolated by default (S1), the fakes are
   refused in production (S2) and the audit log cannot be altered (S3).
2. **The first installation is one dedicated node** (P4): no high availability, a recovery point of
   24 hours, stated in writing. High availability (P2) comes later.
3. **When the first installation and 1.0 compete, the installation wins.** Freezing the interfaces
   (A1) is estimated at 55 to 80 person-days; 1.0 moves from 2027-04-30 to 2027-06-30.

## The workstreams

### Security

| Workstream | Content | Start → end | Effort | Waits for | Done when |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **S2** The fakes are refused in production | Refuse `CHOREGOS_FAKES` in the API, the workers and the chart; move the internal API guard (`CHOREGOS_INTERNAL_API_GUARD`, `apps/api/src/tranibor_api/internal_api_guard.py`, ADR 0144) from `warn`, its default, to `enforce` after two to four weeks of observation | 2026-11-30 → 2027-03-26 | 8 to 14 | nothing | Rendering the chart for production with the fakes on fails |
| **S5** The supply chain is pinned | Actions pinned by commit SHA, base images by digest; then signed charts, high vulnerabilities blocking, static security analysis | 2026-12-14 → 2027-04-16 | 7 to 12 | nothing | A test fails on any unpinned action and any `latest` |
| **S1** The agent is isolated | The agent in its own container by default (ADR 0146, the S65-04 stories); the three real agents behind the launcher; the Kubernetes and Docker executors. Tekton and Azure Container Apps are frozen (ADR 0180) | 2027-01-18 → 2027-04-09 | 30 to 45 | none: `dev` and `prod` have the `gvisor` RuntimeClass (since 2026-10-21 and 2026-10-25) and the runner pods already run under it | Under gVisor, the agent reads the run's token from no process |
| **S3** A tamper-evident audit log | Hash chaining, an append-only table, a verifier, a signed checkpoint; SIEM export and retention in May | 2027-02-15 → 2027-04-09 | 15 to 22 | human review (ADR 0180 §3): the migration cannot be undone | An update to the log by the API's database role is refused |
| **S6** Kyverno policies | Rules on the service account token, host namespaces, a read-only root file system | 2027-04-12 → 2027-05-07 | 6 to 10 | S1, S5 | One refusal per rule, observed on a cluster |
| **S4** Encryption and keys | TLS and a payload codec for Temporal first; per-organisation keys behind a KMS in the autumn | 2027-06-01 → 2027-10-29 | 22 to 38 | S2; the choice of a KMS | No ticket text in clear in an exported history |

Where the code stands on 2026-11-17:

- S2: until S80-03 (2026-11-17) nothing refused `CHOREGOS_FAKES` in staging or production; the render
  of the chart now fails, and the API, the workers and the runner refuse to start, with the fakes on.
  The internal API guard still defaults to `warn`.
- S5: until S80-04 (2026-11-17) none of the 134 `uses:` lines of `.github/workflows/` was pinned by
  SHA, base images were referenced by tag and `docker/runner.Dockerfile` installed `npm@latest`; all
  are pinned now. Signed charts, blocking vulnerabilities and static analysis remain.
- S1: `runner.sandbox.agentIsolation` defaults to `process` (`charts/choregos/values.yaml`); the
  `container` value is served by the `agent_sandbox` executor only (S65-04d, S65-04i).
- S3: `apps/api/src/tranibor_api/audit.py` adds an `AuditLog` row to the session (44 lines); nothing
  chains, freezes or exports it.
- S4: until S80-06 (2026-11-17, ADR 0183) every Temporal client called `Client.connect` in clear,
  with the default converter. Now `choregos_core.temporal_connection.connect_temporal` is the only
  caller, with TLS and an AES-256-GCM payload codec, both off by default; one key serves every
  organisation, and no handshake with a TLS-enabled Temporal has been made.

### Proof and operations

| Workstream | Content | Start → end | Effort | Waits for | Done when |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **P1** Proof on real systems | The local install with a real model (December); ServiceNow on a real instance (January); software delivery end to end on a real repository (March) | 2026-11-23 → 2027-04-30 | 18 to 30 | a ServiceNow development instance; a capped model key | 14 green nights in a row on the three |
| **P4** A single-node mode | The existing chart on one k3s node, with backups. The cheaper option: hardening `docker compose` was estimated at 25 to 40 | 2027-01-18 → 2027-03-05 | 15 to 25 | P1 (the local install) | Every night: a blank machine, a ticket carried to its end, a backup, the machine destroyed, a restore |
| **P2** A preproduction environment | Temporal and PostgreSQL highly available, a load injector, monitored restores. Tiers of 100 and 300 concurrent runs in July, 1,000 in October | 2027-05-03 → 2027-10-29 | 35 to 60 | P3 for the 1,000 tier | 1,000 simulated runs without loss, the primary and a Temporal node killed under load |
| **P3** The orchestrator decoupled | Tunable concurrency, waiting for runs without polling, the ceiling of the orchestrator's imports of the API brought from 156 to 100. Zero waits for 2028: it means moving some forty services out of the API | 2027-05-10 → 2027-11-26 | 45 to 75, 48 kept | nothing | 500 waiting runs held by one worker |

Where the code stands on 2026-11-17:

- P4: `compose.yaml` states that everything in it is "for a laptop". S80-07b (2026-11-17, ADR 0184)
  adds a single-node profile of the chart (`charts/choregos/values/single-node.yaml`) with a nightly
  backup; its blank-machine proof has not run to its end yet.
- P3: the worker's concurrency became a setting on 2026-11-17 (S80-07a: `CHOREGOS_WORKER_MAX_*`,
  defaults 20 activities, 100 workflow tasks, 100 local activities, a sticky cache of 1,000); the
  ceiling in `tests/architecture/test_orchestrator_imports_of_the_api.py` is 156.

### Product

| Workstream | Content | Start → end | Effort | Waits for | Done when |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **A6** The documentation purge | `STATUS.md` split and brought under 300 lines, the backlog resynchronised, the deployment guide up to date | 2026-11-18 → 2026-11-27 | 8 to 12 | nothing | Tests on the status's size and on the version the guide installs |
| **A1** The interfaces frozen (1.0) | The decision on neutral contract vocabulary in December (ADR 0181, proposed); the renames in the core and the enterprise edition; guards on the five surfaces (ADR 0168) | 2026-12-07 → 2027-06-30 | 55 to 80 | A6; human review (ADR 0180 §3) | Removing an operation, an MCP tool or a seam fails CI |
| **A2** User tests | Two rounds of five filmed sessions with outside testers; the first ten minutes | 2027-03-15 → 2027-06-25 | 20 to 30 | a stable install | 4 testers of 5 reach a first decision alone in 10 minutes |
| **A4** Certified MCP connectors | A certification suite; ServiceNow, Microsoft 365, an asset manager, then four more: 7 kept of 10 | 2027-05-10 → 2027-10-29 | 5 to 9 per connector, 48 kept | A1 (the MCP profile frozen, ADR 0169) | Certification replayed every night against real tenants |
| **A3** The console in French | Strings extracted from about 310 files, translated, guarded | 2027-08-16 → 2027-10-29 | 30 to 45 | A2: no translating of screens about to go | A journey in French with no English string |
| **A5** A workflow gallery | 12 workflows on the two entry trades (software delivery, ITSM triage) | 2027-09-06 → 2027-11-26 | 30 kept | A4, otherwise they are demonstrations on fakes | Each one runs to its end on an empty project, with no demonstration connector |

Where the code stands on 2026-11-17:

- A6: `docs/plan/STATUS.md` was 2,209 lines and `docs/deployment.md` installed `--version 0.4.1` while
  `charts/choregos/Chart.yaml` was at 0.27.12; S80-02 split the status into `docs/plan/status/` and
  corrected the guide the same day.
- A1: the contracts carry the software vocabulary: `fix_ci` and `release_captain` in
  `packages/contracts/schemas/workflow.schema.json`, `pr_url` in
  `packages/contracts/schemas/stage-result.schema.json`.

### Editions and streams in flight

| Workstream | Content | Start → end | Effort | Waits for | Done when |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **O3** Streams in flight | Freeze the editor extension, assistants and outposts; finish the streams ADR 0180 lets finish (activity and tokens, the terminal, several repositories) | 2026-11-17 → 2027-04-23 | 12, S1 aside | nothing | No partial story left outside those frozen by name |
| **O4** Ontology and quant | The ontology removed in February; `quant` out to a plugin in June | 2027-02-15 → 2027-06-18 | 8 to 11 | the decision in December (ADR 0180 §4) | The core names no trade, with no exemption |
| **O2** The enterprise edition, deliverable | The core's side: seams renamed (A1), images signed and multi-architecture from a registry reachable outside the project, the SIEM export (S3). The edition's own work lives in its repository | 2027-03-15 → 2027-11-12 | 45 to 70 | S3, S5, A1 | On a cluster foreign to the project, the signature verifies and an expired licence refuses to start |
| **O1** A hosted offer | A second product, later. Its work is dominated by infrastructure and public multi-tenant security; it waits for P2, S4 and 1.0, and is not planned in this roadmap | after 2027-09 | — | P2, S4, 1.0 | Not planned here |

Where the code stands on 2026-11-17: `packages/ontology` and `packages/quant` exist, and
`packages/runner/pyproject.toml` depends on `choregos-quant`.

## The orderings that do not move

| Before | After | Why |
| :-- | :-- | :-- |
| The freeze (ADR 0180) | everything | Thirty pull requests a day redraw what is being stabilised |
| A6 the documentation purge | A1 | A status of 1.5 MB is readable by no newcomer |
| Human review available (ADR 0180 §3) | S3, S4, A1 | Migrations and contracts that cannot be undone need a human reader |
| A1's decision on vocabulary, in December | the renames | A decision after January loses the 1.0 date |
| S2 the fakes refused | any outside installation | Nothing forbids a fake in production today |
| P1 the real local install | P4 the single node | The blank-machine test reuses the same harness |
| P1 ServiceNow on a real instance | the ITSM workflows, A4 | "Certified" is defined by a real run, replayed |
| P4 the single node | J3 | No supported install exists outside Kubernetes |
| S1 the isolation | J3, S6, the penetration test, O1 | Under gVisor, today's mode confines nothing |
| S5 the signatures | S6, O2 | A signature that is absent cannot be verified |
| S3 the audit | regulated installations, the SIEM export | The log can be altered today |
| A1 1.0 | A4, O2 | The MCP profile and the seams must be frozen |
| A2 the user tests | A3, A5 | No translating or publishing of screens about to go |
| A4 the connectors | A5 the gallery | Otherwise they are demonstrations on fakes |
| P3 the decoupling | P2, the 1,000 tier | The worker's waits cap the executors |
| P2, S4, 1.0 | O1 | One key and an unmonitored restore cannot be shared between tenants |

Three chains have no slack. The first installation: the single node, the agent's isolation,
production. 1.0: the purge, the vocabulary decision, a human reviewer, the renames. The hosted offer:
the decoupling, the preproduction environment, per-organisation keys.

## The gates

Each gate opens on measurements, not on a date. Only the technical criteria are listed here.

| Gate | Date | Technical criteria, all measured | If one is missing |
| :-- | :-- | :-- | :-- |
| **J1** Pilotable | 2027-01-15 | 7 green nights of the local install with a real model; ServiceNow green on a real instance; the fakes refused in production; a cluster with gVisor available (met: `dev` and `prod`, checked 2026-11-18) | S1's proof runs on `dev` |
| **J2** Installable | 2027-03-12 | 5 green nights of the single-node test (a blank machine, a backup, a restore); a human review on every change to contracts, security and migrations | The freeze is extended (ADR 0180) |
| **J3** First outside installation in production | 2027-05-14 | The agent isolated by default, its test green under gVisor; the audit log tamper-evident; 14 green nights on real systems; the single-node mode's limits (one node, a 24-hour recovery point) written down | The installation waits. Never ship with the agent not isolated |
| **J4** 1.0 | 2027-06-30 | Guards in place on the five surfaces; no French name left in the seams; 4 testers of 5 succeed alone in 10 minutes | 1.0 slips by a month |
| **J5** Repeatable | 2027-09-30 | The 300-run tier without loss; signatures verified from a registry reachable outside the project; no critical finding of the penetration test open; 4 connectors certified every night | The French console and the hosted offer move out |
| **J6** Wider | 2027-11-30 | 1,000 simulated runs without loss; isolation between two organisations of the hosted offer proved; the French journey green | The hosted offer is reconsidered |

## What this roadmap gives up

- 1.0 on 2027-04-30: it moves to 2027-06-30.
- High availability for the first installations: one node, a 24-hour recovery point.
- Ten connectors and thirty workflows: seven and twelve.
- The editor extension, assistants, outposts, and Tekton and Azure Container Apps for agent isolation:
  frozen (ADR 0180).
- Thirty pull requests a day: the pace drops once human review is mandatory. On purpose.

## Measured along the way

| Measure | 2026-11-17 | J3 (May 2027) | J5 (September 2027) | J6 (November 2027) |
| :-- | :-- | :-- | :-- | :-- |
| Partial stories (🟡 rows of STATUS) | 329 | fewer than 100 | none outside those frozen | none outside those frozen |
| Consecutive green nights on real systems | 0 | 14 | continuous | continuous |
| Concurrent runs proved without loss | not measured | not measured | 300 | 1,000 |

## The first two weeks (technical)

- [x] The freeze written down: no new stream, the editor extension, assistants and outposts frozen
      (ADR 0180, S80-01)
- [x] The partial stories sorted into finish, frozen, abandon (TRIAGE-2026-11-17, S80-01)
- [x] `STATUS.md` split and the deployment guide corrected (S80-02, workstream A6)
- [x] A cluster with gVisor (Kubernetes 1.29 or later): `dev` and `prod` (k3s 1.31, Cilium) have the
      `gvisor` RuntimeClass, and the `choregos-runner` pods run under it (checked 2026-11-18)
- [ ] A ServiceNow development instance, a capped model key: requested
- [ ] The local install run once to its end with a real model, then every night (P1)
