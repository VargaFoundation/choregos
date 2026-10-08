// SPDX-License-Identifier: Apache-2.0
/**
 * Fixtures du mode démo (`NEXT_PUBLIC_API_MODE=mock`).
 *
 * Elles racontent un projet vivant : un ticket en cours d'implémentation, un autre en
 * attente de validation, un train prêt à partir, un finding à trier. Le flux front
 * avance sans l'API — c'est ce que demande la section 3.4 du plan.
 */
import type {
  Action,
  Agent,
  AgentCatalogueEntry,
  AgentCreate,
  ConnectorType,
  OrgConnector,
  ProjectOperation,
  ProjectRequirement,
  AgentCredential,
  AgentMetrics,
  ApiToken,
  ProjectAgent,
  ProjectAgentPut,
  Skill,
  SkillVersion,
  CostReport,
  DoraReport,
  FindingPage,
  MeDto,
  Memory,
  ProjectPage,
  ReleasePage,
  Run,
  RunEventDto,
  TimelineEntry,
  TrainStatus,
  WorkflowDef,
  WorkflowEditResult,
  WorkflowOperation,
  WorkflowRouting,
  WorkflowSummary,
  WorkflowValidation,
  WorkItemDto,
  WorkItemPage,
} from "@/lib/types";
import { parcoursDe, parcoursEnCours, parcoursTermine } from "./parcours";

const now = new Date();
const iso = (minutesAgo: number) => new Date(now.getTime() - minutesAgo * 60_000).toISOString();

const projectConfig = (slug: string) => ({
  slug,
  org: "varga",
  repo: { url: `https://github.com/varga/${slug}.git`, default_branch: "main", language: "python" as const },
});

export const me: MeDto = {
  id: "u1",
  email: "augustin@varga.dev",
  display_name: "Augustin",
  memberships: [
    { org: "varga", role: "org_admin", user_id: "u1", email: "augustin@varga.dev" },
  ],
};

export const projects: ProjectPage = {
  items: [
    {
      id: "p1",
      slug: "billing-api",
      org: "varga",
      name: "Billing API",
      status: "active",
      template_ref: "github-tekton-argo-k8s@1.0.0",
      config: projectConfig("billing-api"),
      workflow_name: "default-simple",
      policy_name: "solo",
      stats: {
        active_work_items: 3,
        cost_month_eur: 42.18,
        budget_month_eur: 150,
        trains_pending: 1,
        first_pass_merge_rate: 0.72,
        cycle_time_p50_hours: 19.5,
      },
      created_at: iso(60 * 24 * 40),
      updated_at: iso(35),
    },
    {
      id: "p2",
      slug: "checkout-web",
      org: "varga",
      name: "Checkout Web",
      status: "provisioning",
      config: projectConfig("checkout-web"),
      workflow_name: "advanced",
      policy_name: "team",
      stats: { active_work_items: 0, cost_month_eur: 0, trains_pending: 0 },
      created_at: iso(120),
      updated_at: iso(12),
    },
  ],
  meta: { has_more: false },
};

export const workItems: WorkItemPage = {
  items: [
    item("w1", "varga/billing-api#123", "Credit notes are not deducted from the total", "in_progress", "In progress", {
      size: "M" as const,
      cost: 6.77,
      run: { id: "r3", status: "running", stage_role: "implement", attempt: 1, cost_usd: 2.74, backend: "claude-code" },
    }),
    item("w2", "varga/billing-api#124", "Export invoices as PDF", "awaiting_spec_approval", "Spec to approve", {
      size: "L",
      cost: 1.12,
      pending: true,
    }),
    item("w3", "varga/billing-api#125", "Fix the rounding of discounts", "pr_open", "PR open", {
      size: "S",
      cost: 3.4,
      pr: "https://github.com/varga/billing-api/pull/456",
    }),
    item("w4", "varga/billing-api#120", "Migrate to pydantic v2", "deployed_prod", "In production", {
      size: "L",
      cost: 24.9,
      closed: true,
    }),
  ],
  meta: { has_more: false },
};

function item(
  id: string,
  key: string,
  title: string,
  state: string,
  display: string,
  extra: {
    size?: "S" | "M" | "L" | "XL";
    cost?: number;
    run?: Partial<Run> & { id: string; status: string; stage_role: string };
    pending?: boolean;
    pr?: string;
    closed?: boolean;
  },
): WorkItemDto {
  return {
    id,
    project_slug: "billing-api",
    tracker_key: key,
    title,
    state,
    state_display: display,
    size: extra.size,
    risk: "low",
    workflow_name: "default-simple",
    workflow_version: 1,
    paused: false,
    pr_url: extra.pr,
    totals: {
      tokens_in: 645_000,
      tokens_out: 35_000,
      tokens_cached: 301_000,
      cost_usd: (extra.cost ?? 0) / 0.92,
      cost_eur: extra.cost ?? 0,
      duration_s: 4800,
      runs: 3,
    },
    estimate: { median_usd: 8.2, p80_usd: 14.1, sample_size: 12, over_p80: false },
    current_run: extra.run
      ? {
          id: extra.run.id,
          status: extra.run.status,
          stage_role: extra.run.stage_role,
          attempt: extra.run.attempt ?? 1,
          backend: extra.run.backend ?? "claude-code",
          model: "anthropic/claude-sonnet-5",
          cost_usd: extra.run.cost_usd ?? 0,
          started_at: iso(6),
        }
      : undefined,
    pending_request: extra.pending
      ? {
          id: "hr1",
          kind: "approval",
          payload: { summary: "Approval of the specification requested" },
          requested_at: iso(180),
        }
      : undefined,
    created_at: iso(60 * 20),
    closed_at: extra.closed ? iso(60 * 2) : undefined,
  };
}

export const timeline: TimelineEntry[] = [
  { ts: iso(240), kind: "state_change", title: "inbox → refining", actor: "orchestrator", actor_kind: "system" },
  {
    ts: iso(235),
    kind: "run",
    title: "refine — attempt 1",
    detail: "spec written, 6 allowed paths",
    actor: "claude-code",
    actor_kind: "agent",
    cost_usd: 0.34,
    ref_id: "r1",
  },
  { ts: iso(200), kind: "decision", title: "approval: approved", actor: "augustin", actor_kind: "user" },
  {
    ts: iso(90),
    kind: "run",
    title: "implement — attempt 1",
    detail: "7 commits, 412 tests passing",
    actor: "claude-code",
    actor_kind: "agent",
    cost_usd: 2.74,
    ref_id: "r3",
  },
  {
    ts: iso(80),
    kind: "finding",
    title: "finding medium: N+1 query on order lines",
    detail: "src/orders/repository.py:88",
    actor_kind: "agent",
  },
];

export const runs: Run[] = [
  run("r1", "refine", "succeeded", 0.34, "spec written"),
  run("r2", "verify", "succeeded", 0.62, "412 tests passing, coverage +1.2"),
  run("r3", "implement", "running", 2.74, "running"),
];

function run(id: string, role: string, status: string, cost: number, summary: string): Run {
  return {
    id,
    status,
    stage_role: role,
    attempt: 1,
    backend: "claude-code",
    model: "anthropic/claude-sonnet-5",
    cost_usd: cost,
    started_at: iso(60),
    work_item_id: "w1",
    project_slug: "billing-api",
    transition_id: `t-${role}`,
    executor_kind: "tekton",
    tokens: {
      tokens_in: 388_000,
      tokens_out: 22_000,
      tokens_cached: 301_000,
      cost_usd: cost,
      cost_eur: cost * 0.92,
      duration_s: 1260,
      runs: 1,
    },
    allowed_paths: ["src/orders/**", "tests/orders/**"],
    result: {
      schema: "choregos/StageResult/v1",
      status: status === "running" ? "done" : "done",
      summary,
      evidence: { tests_passed: true, tests_run: 412, tests_failed: 0, coverage_delta: 1.2, lint: "ok" },
    },
  } as Run;
}

/**
 * Le détail d'un ticket, accordé à son parcours (S22-02) : `#123` court dans `dev-complex`, sa revue de
 * sécurité tourne. La liste du board garde ses fixtures — ses colonnes sont celles du workflow par défaut.
 */
export function ticketDetaille(id: string): WorkItemDto {
  const base = workItems.items.find((i) => i.id === id) ?? workItems.items[0]!;
  if (base.id !== "w1") return base;
  return {
    ...base,
    state: "reviewed",
    state_display: "Reviewed by an agent",
    workflow_name: "dev-complex",
    workflow_version: 1,
    current_run: {
      id: "r-security-review-1",
      status: "running",
      stage_role: "review",
      attempt: 1,
      backend: "claude-code",
      model: "profile:standard",
      cost_usd: 0.21,
      started_at: iso(6),
    },
  };
}

/** Les deux parcours du mode démo, exposés pour la vérification de langue des fixtures. */
export const parcoursDeDemonstration = [parcoursEnCours(), parcoursTermine()];

/** Ce à quoi l'agent a touché, replié depuis son journal (`/runs/{id}/access`). */
export const runAccess = {
  evenements: 64,
  refus: 1,
  acces: [
    { nature: "read", cible: "src/billing/totals.py", demandes: 6, refus: 0 },
    { nature: "write", cible: "src/billing/totals.py", demandes: 3, refus: 0 },
    { nature: "write", cible: "tests/billing/test_totals.py", demandes: 2, refus: 0 },
    { nature: "execute", cible: "make test", demandes: 4, refus: 0 },
    {
      nature: "write",
      cible: "src/billing/rates.py",
      demandes: 1,
      refus: 1,
      motifs: ["outside the allowed paths: use report_finding or request_scope_change"],
    },
  ],
};

/** Ce que le run a changé (`/runs/{id}/diff`). */
export const runDiff = {
  base: "main",
  head: "choregos/billing-api-123",
  additions: 58,
  deletions: 11,
  files: [
    { path: "src/billing/totals.py", status: "modified", additions: 31, deletions: 9, in_scope: true },
    { path: "tests/billing/test_totals.py", status: "modified", additions: 27, deletions: 2, in_scope: true },
  ],
};

export const runEvents: RunEventDto[] = [
  { seq: 1, type: "run.started", ts: iso(60), payload: { role: "implement" } },
  {
    seq: 0,
    type: "gate.outcome",
    ts: iso(30),
    payload: { name: "scope_respected", passed: true, pending: false, detail: "2 files, all within the allowed paths" },
  },
  {
    seq: 0,
    type: "gate.outcome",
    ts: iso(30),
    payload: { name: "evidence_present", passed: false, pending: false, detail: "no test was run (tests_run missing)" },
  },
  { seq: 2, type: "session/update", ts: iso(59), payload: { text: "Reading src/orders/total.py" } },
  {
    seq: 3,
    type: "session/request_permission",
    ts: iso(58),
    payload: { allowed: true, kind: "edit", target: "src/orders/total.py", reason: "within the allowed paths" },
  },
  {
    seq: 4,
    type: "session/request_permission",
    ts: iso(57),
    payload: {
      allowed: false,
      kind: "edit",
      target: "src/billing/rates.py",
      reason: "outside the allowed paths — use report_finding or request_scope_change",
    },
  },
  { seq: 5, type: "dod.retry", ts: iso(40), payload: { iteration: 1, failures: ["tests"] } },
  { seq: 6, type: "session/update", ts: iso(35), payload: { text: "Fixing the rounding and running the tests again" } },
  { seq: 7, type: "run.result", ts: iso(30), payload: { status: "done", summary: "credit notes deducted from the total" } },
];

export const trains: Record<string, TrainStatus> = {
  prod: {
    env: "prod",
    status: "collecting",
    batch_size: 2,
    pending_items: ["varga/billing-api#125", "varga/billing-api#126"],
    next_departure: new Date(now.getTime() + 45 * 60_000).toISOString(),
    frozen: false,
    window_open: true,
  },
  staging: {
    env: "staging",
    status: "verifying",
    batch_size: 0,
    pending_items: [],
    frozen: false,
    window_open: true,
  },
};

export const releases: ReleasePage = {
  items: [
    {
      id: "rel1",
      project_slug: "billing-api",
      env: "prod",
      batch_no: 42,
      status: "done",
      items: [{ work_item_key: "varga/billing-api#120", sha: "a1b2c3", title: "Migrate to pydantic v2" }],
      started_at: iso(60 * 26),
      ended_at: iso(60 * 25),
      approved_by: "marie@varga.dev",
      verdict: { go: true },
    },
    {
      id: "rel2",
      project_slug: "billing-api",
      env: "prod",
      batch_no: 41,
      status: "rolled_back",
      items: [{ work_item_key: "varga/billing-api#118", sha: "d4e5f6" }],
      started_at: iso(60 * 50),
      ended_at: iso(60 * 49),
      verdict: { go: false, reason: "canary analysis failed: 5xx > 1%" },
    },
  ],
  meta: { has_more: false },
};

export const findings: FindingPage = {
  items: [
    {
      id: "f1",
      project_slug: "billing-api",
      title: "N+1 query when loading order lines",
      type: "perf",
      severity: "medium",
      evidence: "src/orders/repository.py:88 — 1 + N queries for 200 lines",
      suggested_fix: "selectinload(Order.lines)",
      estimate: "S",
      status: "pending",
      origin_work_item_key: "varga/billing-api#123",
      occurrences: 2,
      created_at: iso(80),
    },
    {
      id: "f2",
      project_slug: "billing-api",
      title: "Flaky test: test_invoice_totals",
      type: "flaky-test",
      severity: "low",
      evidence: "fails once in 12 CI runs",
      status: "created",
      created_work_item_key: "varga/billing-api#127",
      occurrences: 1,
      created_at: iso(300),
    },
  ],
  meta: { has_more: false },
};

export const memories: Memory[] = [
  {
    id: "m1",
    kind: "decision",
    subject: "decision:billing:totals-rounding",
    content: "Totals are rounded to the cent when an invoice is issued, never when it is displayed (ADR-0007).",
    status: "active",
    valid_from: iso(60 * 24 * 180),
    provenance: { source: "scm", ref: "docs/adr/0007-rounding.md" },
  },
  {
    id: "m2",
    kind: "incident",
    subject: "incident:billing-api:2026-06-11",
    content: "Rolled back after a regression on credit notes: the calculation had moved to the client.",
    status: "active",
    valid_from: iso(60 * 24 * 100),
    provenance: { source: "cd", ref: "R-2026.06.11-1" },
  },
];

export const pendingMemories: Memory[] = [
  {
    id: "m3",
    kind: "convention",
    subject: "convention:tests:given-when-then",
    content: "Acceptance tests follow the Given/When/Then structure.",
    status: "pending",
    proposed_by: "r3",
    provenance: { source: "agent", run_id: "r3" },
  },
];

export const costs: CostReport = {
  group_by: "day",
  currency: "EUR",
  rows: Array.from({ length: 14 }, (_, index) => ({
    key: new Date(now.getTime() - (13 - index) * 86_400_000).toISOString().slice(0, 10),
    cost_usd: 1.2 + Math.sin(index) * 0.8 + index * 0.15,
    cost_eur: (1.2 + Math.sin(index) * 0.8 + index * 0.15) * 0.92,
    tokens_in: 120_000 + index * 8_000,
    tokens_out: 9_000,
    tokens_cached: 60_000,
    runs: 2 + (index % 3),
  })),
  total: { cost_usd: 42.6, cost_eur: 39.2, budget_usd: 150 },
};

export const dora: DoraReport = {
  env: "prod",
  since: new Date(now.getTime() - 30 * 86_400_000).toISOString(),
  until: now.toISOString(),
  deployments: 23,
  deployment_frequency: { value: 0.77, unit: "per day", level: "high", sample: 23 },
  lead_time: { value: 9.4, unit: "hours (median)", level: "elite", sample: 23 },
  change_failure_rate: { value: 0.087, unit: "share of production releases", level: "high", sample: 23 },
  time_to_restore: { value: 1.6, unit: "hours (median)", level: "high", sample: 2 },
};

export const workflow: WorkflowDef = {
  name: "default-simple",
  version: 1,
  source: "template",
  checksum: "sha256:demo",
  is_active: true,
  yaml: `apiVersion: choregos/v1
kind: Workflow
metadata: { name: default-simple, version: 1 }
actors:
  refiner: { type: agent, role: refine, model: "profile:standard" }
  owner: { type: human, group: product-owners, sla_hours: 24 }
  dev: { type: agent, role: implement, model: "profile:by_size" }
states:
  inbox: { display: To triage, kind: wait }
  ready: { display: Ready }
  done: { display: Done, terminal: true }
transitions:
  - { id: t-refine, from: inbox, to: ready, by: refiner }
  - { id: t-implement, from: ready, to: done, by: dev, gates: [scope_respected] }
`,
  json: {
    metadata: { name: "default-simple", version: 1 },
    actors: {
      refiner: { type: "agent", role: "refine", model: "profile:standard" },
      owner: { type: "human", group: "product-owners", sla_hours: 24 },
      dev: { type: "agent", role: "implement", model: "profile:by_size" },
    },
  } as unknown as WorkflowDef["json"],
};

/** Ce que l'API répond à la validation du workflow ci-dessus : la carte de `default-simple`, et sa
 * vue processus. Les arêtes `default` partent de chaque état d'agent : la carte les dit une fois. */
export const workflowValidation: WorkflowValidation = {
  valid: true,
  errors: [],
  warnings: [],
  graph: {
    nodes: [
      { id: "inbox", display: "To triage", kind: "wait", lane: "agent" },
      { id: "ready", display: "Ready", kind: "normal", lane: "agent" },
      { id: "needs_human", display: "Needs a human", kind: "wait", lane: "human" },
      { id: "done", display: "Done", kind: "normal", terminal: true, lane: "terminal" },
    ],
    edges: [
      { id: "t-refine", from: "inbox", to: "ready", kind: "nominal", label: "t-refine", actor: "refiner", gates: [] },
      { id: "t-implement", from: "ready", to: "done", kind: "nominal", label: "t-implement", actor: "dev", gates: ["scope_respected"] },
      // Une escalade : la carte ne la montre qu'autour de son état, ou sur demande.
      { id: "ready->needs_human:escalate:when retries run out", from: "ready", to: "needs_human", kind: "escalate", label: "when retries run out" },
      { id: "inbox->needs_human:default:question", from: "inbox", to: "needs_human", kind: "default", label: "question" },
      { id: "ready->needs_human:default:question", from: "ready", to: "needs_human", kind: "default", label: "question" },
      { id: "ready->needs_human:default:budget", from: "ready", to: "needs_human", kind: "default", label: "budget exceeded" },
    ],
  },
  process: [
    {
      id: "t-refine",
      from: "inbox",
      from_display: "To triage",
      to: "ready",
      to_display: "Ready",
      actor: "refiner",
      actor_type: "agent",
      who: "the agent `refiner` (role refine, model profile:standard)",
      outputs: ["spec_markdown"],
      gates: [],
      on_fail: null,
      on_reject: null,
      timeout_hours: null,
      sentence: "From “To triage”, the agent `refiner` (role refine, model profile:standard) moves the item to “Ready” once it produced `spec_markdown`.",
    },
    {
      id: "t-implement",
      from: "ready",
      from_display: "Ready",
      to: "done",
      to_display: "Done",
      actor: "dev",
      actor_type: "agent",
      who: "the agent `dev` (role implement, model profile:by_size)",
      outputs: [],
      gates: [{ name: "scope_respected", summary: "the agent stayed within its allowed paths" }],
      on_fail: "On failure it retries up to 2 time(s) from “Ready”, then goes to “Needs a human”.",
      on_reject: null,
      timeout_hours: 72,
      sentence: "From “Ready”, the agent `dev` (role implement, model profile:by_size) moves the item to “Done” once the agent stayed within its allowed paths. It times out after 72 h.",
    },
  ],
};

/** Les workflows du projet de démonstration : le défaut, et un flux d'incident routé par étiquette. */
export const workflowSummaries: WorkflowSummary[] = [
  { name: "default-simple", version: 1, is_default: true, open_items: 2, created_by: "lea@varga.dev", updated_at: iso(60 * 24 * 3) },
  { name: "hotfix", version: 2, is_default: false, open_items: 0, created_by: "marc@varga.dev", updated_at: iso(60 * 5) },
];

export const workflowRouting: WorkflowRouting = {
  default: "default-simple",
  rules: [{ when: { labels_any: ["incident"], labels_all: [], item_type: null }, workflow: "hotfix" }],
};

/** Le flux d'incident : ses demandes portent des champs, que le formulaire déduit de `inputs`. */
export const hotfix: WorkflowDef = {
  ...workflow,
  name: "hotfix",
  version: 2,
  is_default: false,
  json: {
    metadata: {
      name: "hotfix",
      version: 2,
      inputs: {
        type: "object",
        required: ["incident_id", "severity"],
        properties: {
          incident_id: { type: "string", title: "incident id" },
          severity: { type: "string", enum: ["sev1", "sev2", "sev3"] },
          detected_on: { type: "string", format: "date", title: "detected on" },
        },
      },
    },
  } as unknown as WorkflowDef["json"],
};

/**
 * Un workflow de dix-huit états (S21-07) : treize sur le chemin nominal, cinq à côté (cadrage repris,
 * réponses à la revue, correction de CI, intervention humaine, abandon). C'est la taille où l'ancienne
 * carte ne tenait plus : elle doit se lire sans zoom.
 */
const ETATS_LONGS: [string, string, string][] = [
  ["inbox", "To triage", "agent"],
  ["triaged", "Triaged", "agent"],
  ["awaiting_spec_approval", "Spec to approve", "human"],
  ["planning", "Planning", "agent"],
  ["ready", "Ready", "agent"],
  ["in_progress", "In progress", "agent"],
  ["verifying", "Verifying", "agent"],
  ["agent_review", "Agent review", "system"],
  ["pr_open", "PR open", "system"],
  ["merged", "Merged", "train"],
  ["deployed_staging", "In staging", "train"],
  ["deployed_prod", "In production", "agent"],
  ["verified_prod", "Verified in production", "terminal"],
  ["refining", "Refining", "agent"],
  ["addressing_review", "Addressing the review", "agent"],
  ["fixing_ci", "Fixing CI", "system"],
  ["needs_human", "Needs a human", "human"],
  ["abandoned", "Abandoned", "terminal"],
];
const nominale = (id: string, from: string, to: string, actor: string | null, gates: string[] = [], via: string | null = null) => ({
  id, from, to, kind: "nominal", label: actor ?? via ?? "", wildcard: false, actor, gates, via, timeout_hours: null,
});
const issue = (from: string, to: string, kind: string, label: string) => ({ id: `${from}->${to}:${kind}:${label}`, from, to, kind, label, wildcard: false, gates: [] });
export const releaseFullValidation: WorkflowValidation = {
  valid: true,
  errors: [],
  warnings: [],
  graph: {
    nodes: ETATS_LONGS.map(([id, display, lane]) => ({ id, display, lane, kind: lane === "terminal" ? "terminal" : "work", terminal: lane === "terminal" })),
    edges: [
      nominale("t-triage", "inbox", "triaged", "sorter"),
      nominale("t-refine", "triaged", "awaiting_spec_approval", "refiner"),
      nominale("t-approve-spec", "awaiting_spec_approval", "planning", "owner"),
      issue("awaiting_spec_approval", "refining", "reject", "if rejected"),
      nominale("t-refine-again", "refining", "awaiting_spec_approval", "refiner"),
      nominale("t-plan", "planning", "ready", "planner"),
      nominale("t-implement", "ready", "in_progress", "dev", ["scope_respected", "no_secrets", "diff_size_max"]),
      issue("ready", "ready", "retry", "on failure (≤3)"),
      issue("ready", "needs_human", "escalate", "when retries run out"),
      nominale("t-verify", "in_progress", "verifying", "checker", ["evidence_present", "coverage_delta_min"]),
      nominale("t-agent-review", "verifying", "agent_review", "reviewer"),
      issue("verifying", "addressing_review", "retry", "on changes requested"),
      nominale("t-address-review", "addressing_review", "verifying", "responder"),
      nominale("t-open-pr", "agent_review", "pr_open", "ci", ["evidence_present", "scope_respected"]),
      nominale("t-fix-ci", "pr_open", "fixing_ci", "fixer"),
      nominale("t-back-to-pr", "fixing_ci", "pr_open", "ci"),
      nominale("t-merge", "pr_open", "merged", "ci", ["ci_green", "review_approved", "scans_ok", "provenance_signed"]),
      nominale("t-deploy-staging", "merged", "deployed_staging", null, [], "release_train"),
      nominale("t-deploy-prod", "deployed_staging", "deployed_prod", null, [], "release_train"),
      nominale("t-verify-prod", "deployed_prod", "verified_prod", "sentinel"),
      nominale("t-human-review", "needs_human", "in_progress", "maintainer"),
      issue("needs_human", "abandoned", "reject", "if rejected"),
      issue("inbox", "needs_human", "default", "question"),
      issue("ready", "needs_human", "default", "question"),
    ],
  },
  process: [],
};
const releaseFull: WorkflowDef = {
  ...workflow,
  name: "release-full",
  version: 1,
  is_default: false,
  yaml: "apiVersion: choregos/v1\nkind: Workflow\nmetadata: { name: release-full, version: 1 }\nstates: {}\n",
  json: { metadata: { name: "release-full", version: 1 }, initial: "inbox", actors: { owner: { type: "human", group: "product-owners", sla_hours: 24 } } } as unknown as WorkflowDef["json"],
};

/** Deux versions : la v2, active, ajoute une garantie au passage en revue. */
export const workflowVersions: WorkflowDef[] = [
  {
    ...workflow,
    version: 2,
    is_active: true,
    checksum: "sha256:v2demo",
    created_by: "marc@varga.dev",
    created_at: iso(60 * 5),
    yaml: workflow.yaml.replace("gates: [scope_respected]", "gates: [scope_respected, ci_green]"),
  },
  { ...workflow, version: 1, is_active: false, created_by: "lea@varga.dev", created_at: iso(60 * 24 * 3) },
];

/** Une section qu'un greffon déclarerait (ADR 0032) : l'exemple SCIM du contrat. */
export const sectionScim = {
  id: "scim",
  title: "SCIM provisioning",
  description: "Your identity provider creates and removes members through SCIM.",
  scope: "organisation",
  permission: "member:manage",
  blocks: [
    {
      kind: "form",
      title: "settings",
      schema: {
        type: "object",
        properties: {
          enabled: { type: "boolean", title: "accept SCIM requests" },
          default_role: { type: "string", enum: ["viewer", "developer"], title: "role of a provisioned member" },
        },
      },
      read: "/orgs/{org}/scim/settings",
      write: "/orgs/{org}/scim/settings",
    },
    {
      kind: "table",
      title: "tokens",
      list: "/orgs/{org}/scim/tokens",
      columns: [
        { key: "name", label: "name" },
        { key: "created_at", label: "created", format: "date" },
      ],
      row_actions: [
        {
          kind: "action",
          label: "revoke",
          path: "/orgs/{org}/scim/tokens/{id}",
          method: "DELETE",
          confirm: "The identity provider stops provisioning with this token.",
          danger: true,
        },
      ],
    },
    {
      kind: "secret_once",
      label: "create a token",
      path: "/orgs/{org}/scim/tokens",
      secret_field: "token",
      params: { type: "object", required: ["name"], properties: { name: { type: "string" } } },
    },
  ],
};
const ETAT_EN_LIGNE = /^( {2}([\w-]+): \{ display: )([^,}]+?)(\s*[,}])/gm;

/**
 * Une greffe simulée (S16-11) : la maquette sait changer le libellé d'un état, et laisse le reste du
 * texte tel quel. Comme l'API, elle rend le texte, le diff, la carte relue et l'inverse du geste.
 */
function editionSimulee(yaml: string, operations: WorkflowOperation[]): WorkflowEditResult {
  let texte = yaml;
  const inverse: WorkflowOperation[] = [];
  for (const op of operations) {
    if (op.op !== "set_state" || op.field !== "display") continue;
    texte = texte.replace(ETAT_EN_LIGNE, (ligne, debut: string, nom: string, ancien: string, fin: string) => {
      if (nom !== op.name) return ligne;
      inverse.unshift({ op: "set_state", name: nom, field: "display", value: ancien });
      return `${debut}${String(op.value)}${fin}`;
    });
  }
  const libelles = new Map([...texte.matchAll(ETAT_EN_LIGNE)].map((m) => [m[2], m[3]]));
  const avant = yaml.split("\n");
  const apres = texte.split("\n");
  const changees = avant.flatMap((ligne, index) => (ligne === apres[index] ? [] : [`-${ligne}`, `+${apres[index]}`]));
  const carte = workflowValidation.graph ?? { nodes: [], edges: [] };
  return {
    ...workflowValidation,
    graph: { ...carte, nodes: carte.nodes.map((n) => ({ ...n, display: libelles.get(n.id) ?? n.display })) },
    process: workflowValidation.process?.map((etape) => ({
      ...etape,
      from_display: libelles.get(etape.from) ?? etape.from_display,
      to_display: libelles.get(etape.to) ?? etape.to_display,
    })),
    yaml: texte,
    diff: changees.length ? `--- workflow.yaml\n+++ workflow.yaml\n${changees.join("\n")}\n` : "",
    inverse,
    notices: [],
  };
}

/** Le registre de démonstration (ADR 0033) : un agent interne qui porte une skill, et un Claude Code. */
export const agents: Agent[] = [
  {
    slug: "onboarding-coordinator",
    kind: "internal",
    display_name: "Onboarding coordinator",
    description: "Prepares a joiner's access plan and follows each step up to the badge.",
    status: "active",
    owner: "lea@varga.dev",
    latest_version: 2,
    created_at: iso(60 * 24 * 9),
    versions: [
      {
        version: 2,
        spec: {
          instructions: "You prepare a joiner's access plan: accounts, groups, laptop, badge.\nCite the access profile you chose.",
          model: "profile:standard",
          limits: { max_turns: 40, max_minutes: 20 },
          budget: { run_usd: 1.5, daily_usd: 5 },
          skills: [{ slug: "onboarding-procedure", version: 1 }],
          mcp_servers: [],
        },
        checksum: "sha256:9c1d0f4e2b7a6c3d5e8f1a2b3c4d5e6f",
        created_by: "lea@varga.dev",
        created_at: iso(60 * 24 * 2),
      },
      {
        version: 1,
        spec: { instructions: "You prepare a joiner's access plan.", model: "profile:standard" },
        checksum: "sha256:1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d",
        created_by: "lea@varga.dev",
        created_at: iso(60 * 24 * 9),
      },
    ],
  },
  {
    slug: "leas-claude-code",
    kind: "external",
    display_name: "Léa's Claude Code",
    status: "active",
    owner: "lea@varga.dev",
    latest_version: 1,
    created_at: iso(60 * 24),
    versions: [
      {
        version: 1,
        spec: { mcp_servers: [{ connector: "choregos", tools: ["list_*", "search_*", "get_*", "create_work_item"] }] },
        checksum: "sha256:5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
        created_by: "lea@varga.dev",
        created_at: iso(60 * 24),
      },
    ],
  },
];

/** Le catalogue de la plateforme (ADR 0040) : quelques entrées, l'une installée, les clients externes. */
const entreeDuCatalogue = (slug: string, display_name: string, role: string, summary: string, extra: Partial<AgentCatalogueEntry> = {}): AgentCatalogueEntry => ({
  slug, kind: "internal", display_name, role, summary, version: 1, skills: [], installed: false, installed_version: null,
  own_agent: false, update_available: false, offered: true, unavailable_reason: null, client: null, reach: null, ...extra,
});
export const agentCatalogue: AgentCatalogueEntry[] = [
  entreeDuCatalogue("developer", "Developer", "implement", "Implements the specification inside the allowed paths, test first, small conventional commits."),
  entreeDuCatalogue("tester", "Tester", "verify", "Runs the full checks, writes the missing tests for each acceptance criterion, never fixes application code.", { installed: true, installed_version: 1 }),
  entreeDuCatalogue("reviewer", "Reviewer", "review", "Reviews the branch with fresh eyes and gives a verdict.", { installed: true, installed_version: 1, update_available: true, skills: ["madr-4"] }),
  entreeDuCatalogue("architect", "Architect", "architect", "Writes one architecture decision record in MADR 4 format; once approved, marks it accepted.", { skills: ["madr-4"] }),
  entreeDuCatalogue("claude-code", "Claude Code", "external", "Claude Code acting through the MCP gate, with its person's rights.", { kind: "external", client: "claude-code", reach: "always" }),
  entreeDuCatalogue("chatgpt", "ChatGPT", "external", "ChatGPT acting through the MCP gate, with its person's rights.", {
    kind: "external", client: "chatgpt", reach: "cloud", offered: false, unavailable_reason: "the MCP gate does not accept OAuth (`global.mcp.oauth`); the console's public address is not https",
  }),
];

export const agentMetrics: AgentMetrics = {
  agent: "onboarding-coordinator",
  days: 30,
  runs: 14,
  succeeded: 12,
  failed: 2,
  success_rate: 12 / 14,
  cost_usd: 6.42,
  cost_by_kind: { model: 5.9, tool: 0.52 },
  by_project: [{ project: "billing-api", runs: 14, cost_usd: 6.42 }],
  spent_today_usd: 0.8,
  daily_budget_usd: 5,
};

/** Le Claude Code de Léa a appelé la porte il y a trois minutes : la console le dit connecté. */
export const agentCredentials: AgentCredential[] = [
  {
    id: "cred-1",
    kind: "token",
    token_id: "tok-claude",
    token_name: "claude-code",
    created_by: "lea@varga.dev",
    created_at: iso(60 * 24),
    last_used_at: iso(3),
    last_client: "claude-code/2.1.0",
  },
];

export const myTokens: ApiToken[] = [
  {
    id: "tok-claude",
    name: "claude-code",
    created_at: iso(60 * 24),
    last_used_at: iso(3),
    last_client: "claude-code/2.1.0",
    scopes: ["mcp:write"],
    project: null,
  },
  { id: "tok-cursor", name: "cursor", created_at: iso(60 * 2), last_used_at: null, scopes: ["mcp:read"], project: null },
  { id: "tok-ci", name: "ci", created_at: iso(60 * 24 * 30), last_used_at: iso(60), scopes: ["*"], project: null },
];

export const projectAgents: ProjectAgent[] = [
  {
    agent: "onboarding-coordinator",
    version: 2,
    overrides: { budget: { daily_usd: 3 } },
    effective: { ...agents[0]!.versions![0]!.spec, budget: { run_usd: 1.5, daily_usd: 3 } },
  },
];

export const skills: Skill[] = [
  {
    slug: "onboarding-procedure",
    description: "The onboarding procedure: accounts, groups, laptop, badge, and who approves what.",
    status: "active",
    latest_version: 1,
    used_by: ["onboarding-coordinator@2"],
    versions: [{ version: 1, digest: "sha256:7f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c", created_by: "lea@varga.dev", created_at: iso(60 * 24 * 3) }],
  },
];

export const skillVersion: SkillVersion = {
  version: 1,
  digest: "sha256:7f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c",
  created_by: "lea@varga.dev",
  created_at: iso(60 * 24 * 3),
  files: {
    "SKILL.md": "---\nname: onboarding-procedure\ndescription: The onboarding procedure.\n---\n\n# Onboarding\n\n1. Entra accounts ten days before the start date.\n2. Groups according to the access profile.\n",
    "profiles.md": "| role | groups |\n|---|---|\n| developer | devs, vpn |\n",
  },
};

/** Des types tirés du registre (ADR 0034) : jira est disponible, ses secrets se nomment. */
const objet = (properties: Record<string, unknown>, required: string[] = []) => ({ type: "object", properties, required });
export const connectorTypes: ConnectorType[] = [
  { kind: "tracker", type: "internal", display: "internal (Choregos holds the work items)", config_schema: objet({}), capabilities: ["tracker"], secret_fields: [] },
  {
    kind: "tracker",
    type: "jira",
    display: "Jira Cloud",
    config_schema: objet({ base_url: { type: "string" }, email: { type: "string" }, project_key: { type: "string" } }, ["base_url", "email", "project_key"]),
    capabilities: ["tracker"],
    secret_fields: ["api_token", "webhook_secret"],
  },
  { kind: "scm", type: "github", display: "GitHub (App choregos-bot)", config_schema: objet({ repo: { type: "string" } }, ["repo"]), capabilities: ["scm"], secret_fields: [] },
  { kind: "ci", type: "tekton", display: "Tekton Pipelines", config_schema: objet({ namespace: { type: "string" } }), capabilities: ["ci"], secret_fields: [] },
  { kind: "cd", type: "argocd", display: "Argo CD + Rollouts", config_schema: objet({ gitops_repo: { type: "string" } }, ["gitops_repo"]), capabilities: ["cd"], secret_fields: ["token"] },
  { kind: "runtime", type: "k8s_job", display: "Kubernetes Job", config_schema: objet({ service_account: { type: "string" } }), capabilities: ["runtime"], secret_fields: [] },
  { kind: "gateway", type: "litellm", display: "LiteLLM", config_schema: objet({ base_url: { type: "string" } }), capabilities: ["gateway"], secret_fields: ["master_key"] },
  { kind: "notify", type: "slack", display: "Slack", config_schema: objet({ channel: { type: "string" } }), capabilities: ["notify"], secret_fields: ["webhook_url", "bot_token"] },
  { kind: "identity", type: "entra", display: "Microsoft Entra ID", config_schema: objet({ tenant: { type: "string" } }, ["tenant"]), capabilities: ["identity"], secret_fields: ["client_secret"] },
];

/** Un annuaire de l'organisation (ADR 0034) : chaque opération porte sa politique. */
export const orgConnectors: OrgConnector[] = [
  {
    name: "entra-acme",
    kind: "identity",
    type: "entra",
    config: { tenant: "acme.onmicrosoft.com" },
    secret_refs: { client_secret: "env:ENTRA_CLIENT_SECRET" },
    status: "ok",
    created_by: "lea@varga.dev",
    operations: [
      { name: "read_user", access: "read", policy: "allowed", groups: [] },
      { name: "create_account", access: "write", policy: "approval", groups: ["hr"], description: "creates an account" },
      { name: "disable_account", access: "write", policy: "approval", groups: ["hr"] },
    ],
  },
];

orgConnectors.push({
  name: "supplier-agent",
  kind: "mcp",
  type: "mcp",
  config: { url: "https://supplier.example/mcp" },
  secret_refs: { token: "env:SUPPLIER_TOKEN" },
  status: "ok",
  operations: [
    { name: "track_order", access: "read", policy: "allowed", groups: [], schema_digest: "sha256:5e1f" },
    { name: "order_laptop", access: "write", policy: "forbidden", groups: [], schema_digest: "sha256:a07c", description: "orders a laptop" },
  ],
});

export const projectOperations: ProjectOperation[] = [
  { connector: "entra-acme", operation: "read_user", access: "read", org_policy: "allowed", project_policy: "approval", effective_policy: "approval" },
  { connector: "entra-acme", operation: "create_account", access: "write", org_policy: "approval", project_policy: null, effective_policy: "approval" },
];

/** Des actions gouvernées (ADR 0035) : une en attente, proposée par un agent ; une faite ; une défaite. */
export const actions: Action[] = [
  {
    id: "act-poste",
    origin: "tool",
    kind: "supplier-agent.order_laptop",
    title: "Order Léa's laptop (supplier)",
    justification: "proposed by the agent onboarding-coordinator in run r9",
    params: { arguments: { model: "laptop-14" } },
    effects: [{ effect: "connector.call", with: { connector: "supplier-agent", operation: "order_laptop", arguments: { model: "laptop-14" } } }],
    proposed_by: { kind: "agent", id: "agent:onboarding-coordinator", run_id: "r9" },
    approval: { approvers: [{ role: "project_owner", min: 1 }], step_up_minutes: 10, separation_of_duties: true },
    decisions: [],
    status: "pending_approval",
    project_slug: "billing-api",
    created_at: iso(12),
    journal: [],
  },
  {
    id: "act-comptes",
    origin: "transition",
    kind: "onboarding.accounts",
    title: "Léa's accounts",
    params: { upn: "lea@acme.example" },
    effects: [
      { effect: "connector.call", with: { connector: "entra-acme", operation: "create_user" }, compensate: { effect: "connector.call", with: { operation: "disable_user" } } },
      { effect: "connector.call", with: { connector: "entra-acme", operation: "add_to_group" } },
    ],
    proposed_by: { kind: "system", id: "system" },
    approval: { step_up_minutes: 10 },
    decisions: [{ by: "lea@varga.dev", decision: "approve", at: iso(90), auth_age_seconds: 120 }],
    status: "succeeded",
    project_slug: "billing-api",
    created_at: iso(95),
    finished_at: iso(88),
    journal: [
      { position: 0, key: "act-comptes:0", effect: "connector.call", status: "done", attempts: 1 },
      { position: 1, key: "act-comptes:1", effect: "connector.call", status: "done", attempts: 2 },
    ],
  },
];

/** Une action de l'ontologie (S20-08), proposée par une personne depuis son client MCP. */
actions.push({
  id: "pr1",
  origin: "ontology",
  kind: "ontology.open_infra_pr",
  title: "open_infra_pr on os-reboot-required",
  justification: "Two nodes have been waiting for a reboot for a week: a pull request schedules the window.",
  params: {
    ontologie: {
      version_id: "v1",
      action_type: "open_infra_pr",
      target_ids: ["os-reboot-required"],
      params: { path: "platform/maintenance/os-reboot-required.yaml" },
      idempotency_key: "os-reboot-required:platform/maintenance/os-reboot-required.yaml",
    },
  },
  effects: [
    { effect: "ontology.effet", with: { index: 0 } },
    { effect: "ontology.preuve", with: { index: 0, position: 1 } },
  ],
  proposed_by: { kind: "user", id: "lea@varga.dev", via: "mcp" },
  approval: { approvers: [{ role: "project_owner", min: 1 }], step_up_minutes: 10, separation_of_duties: true },
  decisions: [],
  status: "pending_approval",
  project_slug: "billing-api",
  created_at: iso(42),
  journal: [],
});

/** Ce que les workflows de Billing API exigent : du logiciel, donc un dépôt, une CI, un train. */
export const projectRequirements: ProjectRequirement[] = [
  {
    capability: "tracker",
    reasons: ["work items live in a tracker: the internal one unless you connect another"],
    connector: { id: "c-tracker", kind: "tracker", type: "internal", config: {}, secret_refs: {}, status: "ok" },
    default_type: null,
  },
  { capability: "scm", reasons: ["default-simple: the guarantee scope_respected on t-implement"], connector: null, default_type: "github" },
  { capability: "ci", reasons: ["hotfix: the guarantee ci_green on t-ship"], connector: null, default_type: "tekton" },
  { capability: "runtime", reasons: ["default-simple: the agent dev runs somewhere"], connector: null, default_type: "k8s_job" },
  { capability: "gateway", reasons: ["default-simple: the agent dev calls models"], connector: null, default_type: "litellm" },
];

/** Routeur des fixtures : reproduit les chemins de l'API réelle. */
export async function mockApi<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  await new Promise((resolve) => setTimeout(resolve, 40));
  if (method === "POST" && path === "/workflows/validate") {
    // Comme l'API : un texte sans `states:` n'est pas un workflow, et ne se dessine pas.
    const texte = String((JSON.parse(String(init.body ?? "{}")) as { yaml?: string }).yaml ?? "");
    if (/name: release-full/.test(texte)) return releaseFullValidation as T;
    if (!/^states:/m.test(texte)) {
      return { valid: false, errors: [{ code: "workflow.states", message: "a workflow declares its states", line: 1, column: 1 }], warnings: [] } as T;
    }
    return workflowValidation as T;
  }
  if (method === "POST" && /^\/orgs\/[^/]+\/scim\/tokens$/.test(path)) {
    // Le secret n'existe qu'ici, une fois : la console le montre et ne le garde pas.
    return { id: "t2", name: "okta", token: "scim_demo_shown_once" } as T;
  }
  if (method === "POST" && path === "/me/tokens") {
    // Le jeton n'est rendu qu'une fois, à la création : la page Integrations le glisse dans ses extraits.
    const corps = JSON.parse(String(init.body ?? "{}")) as { name?: string; scopes?: string[]; project?: string | null };
    return {
      id: "tok-demo",
      name: corps.name ?? "demo",
      created_at: new Date().toISOString(),
      scopes: corps.scopes ?? ["*"],
      project: corps.project ?? null,
      token: "chg_demo_jeton_affiche_une_fois",
    } as T;
  }
  if (method === "POST" && path === "/workflows/edit") {
    const corps = JSON.parse(String(init.body ?? "{}")) as { yaml: string; operations: WorkflowOperation[] };
    return editionSimulee(corps.yaml, corps.operations) as T;
  }
  if (method === "PUT" && /^\/projects\/[^/]+\/workflows\/[^/]+$/.test(path)) {
    // Publier crée la version suivante de celle qui a été lue (S16-02).
    const corps = JSON.parse(String(init.body ?? "{}")) as { yaml: string; base_version?: number | null };
    return { ...workflow, yaml: corps.yaml, version: (corps.base_version ?? workflow.version) + 1 } as T;
  }
  const [chemin] = path.split("?");
  const agentEcrit = /^\/orgs\/[^/]+\/agents\/([^/]+)(\/[a-z]+)?$/.exec(chemin ?? "");
  if (method === "POST" && /^\/orgs\/[^/]+\/agents$/.test(chemin ?? "")) {
    const corps = JSON.parse(String(init.body ?? "{}")) as AgentCreate;
    return {
      slug: corps.slug,
      kind: corps.kind ?? "internal",
      display_name: corps.display_name,
      description: corps.description ?? null,
      status: "active",
      owner: me.email,
      latest_version: 1,
      versions: [{ version: 1, spec: corps.spec ?? {}, checksum: "sha256:nouvelle", created_by: me.email }],
    } as T;
  }
  const duCatalogue = /^\/orgs\/[^/]+\/agent-catalogue\/([^/]+)\/(install|connect)$/.exec(chemin ?? "");
  if (method === "POST" && duCatalogue) {
    const entree = agentCatalogue.find((e) => e.slug === duCatalogue[1])!;
    const agent = {
      slug: entree.slug, kind: entree.kind, display_name: entree.display_name, status: "active", owner: null, latest_version: 1,
      versions: [{ version: 1, spec: {}, checksum: "sha256:catalogue", created_by: `catalogue:${entree.slug}@1` }],
    };
    if (duCatalogue[2] === "install") return agent as T;
    const lecture = (JSON.parse(String(init.body ?? "{}")) as { read_only?: boolean }).read_only;
    return {
      agent,
      token: { id: "tok-catalogue", name: entree.display_name, created_at: iso(0), scopes: [lecture ? "mcp:read" : "mcp:write"], token: "chg_demo_shown_once" },
      oauth_client_id: null,
    } as T;
  }
  if (method === "POST" && agentEcrit?.[2] === "/versions") {
    const agent = agents.find((a) => a.slug === agentEcrit[1]) ?? agents[0]!;
    return { version: agent.latest_version + 1, spec: JSON.parse(String(init.body ?? "{}")), checksum: "sha256:suivante" } as T;
  }
  if (method === "POST" && agentEcrit?.[2] === "/credentials") {
    const corps = JSON.parse(String(init.body ?? "{}")) as { token_id?: string };
    const jeton = myTokens.find((t) => t.id === corps.token_id);
    return { id: "cred-2", kind: "token", token_id: corps.token_id, token_name: jeton?.name, created_by: me.email } as T;
  }
  if (method === "PATCH" && agentEcrit && !agentEcrit[2]) {
    const agent = agents.find((a) => a.slug === agentEcrit[1]) ?? agents[0]!;
    return { ...agent, ...(JSON.parse(String(init.body ?? "{}")) as object) } as T;
  }
  if (method === "PUT" && /^\/projects\/[^/]+\/agents\/[^/]+$/.test(chemin ?? "")) {
    const corps = JSON.parse(String(init.body ?? "{}")) as ProjectAgentPut;
    return { agent: chemin?.split("/").at(-1), version: corps.version, overrides: corps.overrides ?? {}, effective: {} } as T;
  }
  if (method === "POST" && /^\/orgs\/[^/]+\/skills\/import$/.test(chemin ?? "")) return skills[0] as T;
  const decision = /^\/projects\/[^/]+\/actions\/([^/]+)\/decision$/.exec(chemin ?? "");
  if (method === "POST" && decision) {
    const corps = JSON.parse(String(init.body ?? "{}")) as { decision: string; reason?: string };
    const action = actions.find((a) => a.id === decision[1]) ?? actions[0]!;
    return {
      ...action,
      status: corps.decision === "approve" ? "approved" : "rejected",
      decisions: [{ by: me.email, decision: corps.decision, reason: corps.reason ?? null, at: new Date().toISOString() }],
      temporal_wf_id: `action-${action.id}`,
    } as T;
  }
  if (method === "POST" && /^\/orgs\/[^/]+\/connectors\/[^/]+\/discover$/.test(chemin ?? "")) {
    return { added: ["annuler_commande"], changed: ["order_laptop"], removed: [], unchanged: 1 } as T;
  }
  if (method !== "GET") return { ok: true } as T;
  const [route] = path.split("?");
  const actionLue = /^\/projects\/[^/]+\/actions\/([^/]+)$/.exec(route ?? "");
  if (actionLue) return (actions.find((a) => a.id === actionLue[1]) ?? actions[0]) as T;
  if (/^\/orgs\/[^/]+\/actions$/.test(route ?? "")) {
    const statut = new URLSearchParams(path.split("?")[1] ?? "").get("status");
    return actions.filter((a) => !statut || a.status === statut) as T;
  }
  const agentLu = /^\/orgs\/[^/]+\/agents\/([^/]+)$/.exec(route ?? "");
  if (agentLu) return (agents.find((a) => a.slug === agentLu[1]) ?? agents[0]) as T;
  // Le parcours d'un ticket et son détail, par identifiant : chacun le sien (S22-02).
  const parcours = /^\/work-items\/([^/]+)\/journey$/.exec(route ?? "");
  if (parcours) return parcoursDe(parcours[1]!) as T;
  const ticket = /^\/work-items\/([^/]+)$/.exec(route ?? "");
  if (ticket) return ticketDetaille(ticket[1]!) as T;
  const credentials = /^\/orgs\/[^/]+\/agents\/([^/]+)\/credentials$/.exec(route ?? "");
  if (credentials) return (credentials[1] === "leas-claude-code" ? agentCredentials : []) as T;
  const table: Array<[RegExp, unknown]> = [
    [/^\/me$/, me],
    [/^\/edition$/, { edition: "community", features: [], version: "0.13.1" }],
    [
      /^\/integrations$/,
      { mcp_url: "http://localhost:3000/mcp", protocol_versions: ["2025-11-25", "2025-06-18"], oauth: { enabled: false }, version: "0.13.1" },
    ],
    [/^\/me\/tokens$/, myTokens],
    [/^\/orgs\/[^/]+\/agents$/, agents],
    [/^\/orgs\/[^/]+\/agent-catalogue$/, agentCatalogue],
    [/^\/orgs\/[^/]+\/agents\/[^/]+\/metrics$/, agentMetrics],
    [/^\/projects\/[^/]+\/agents$/, projectAgents],
    [/^\/orgs\/[^/]+\/skills$/, skills],
    [/^\/orgs\/[^/]+\/skills\/[^/]+\/versions\/\d+$/, skillVersion],
    [/^\/orgs\/[^/]+\/skills\/[^/]+$/, skills[0]],
    [/^\/ui\/admin-sections$/, [sectionScim]],
    [/^\/orgs\/[^/]+\/scim\/settings$/, { enabled: true, default_role: "viewer" }],
    [/^\/orgs\/[^/]+\/scim\/tokens$/, { items: [{ id: "t1", name: "okta", created_at: iso(60 * 24 * 12) }] }],
    [/^\/orgs$/, [{ slug: "varga", name: "Varga Foundation", role: "org_admin" }]],
    [/^\/orgs\/[^/]+\/members$/, me.memberships],
    [/^\/templates$/, [{ name: "github-tekton-argo-k8s", version: "1.0.0", display: "GitHub · Tekton · Argo CD · Kubernetes", is_published: true }]],
    [/^\/connectors\/types$/, connectorTypes],
    [/^\/projects\/[^/]+\/requirements$/, projectRequirements],
    [/^\/orgs\/[^/]+\/connectors$/, orgConnectors],
    [/^\/projects\/[^/]+\/actions$/, actions],
    [/^\/projects\/[^/]+\/operations$/, projectOperations],
    [/^\/platform\/models$/, []],
    [/^\/platform\/backends$/, []],
    [/^\/platform\/executors$/, []],
    [/^\/projects\/[^/]+\/models$/, { profiles: {}, allow_unvalidated: false, inherited: {} }],
    [/^\/projects\/[^/]+\/provision$/, { project_id: "p1", status: "succeeded", steps: [] }],
    [/^\/projects\/[^/]+\/policy$/, { name: "solo", version: 1, yaml: "budgets:\n  per_ticket_usd: 25\n", is_active: true }],
    [/^\/orgs\/[^/]+\/projects$/, projects],
    [/^\/projects\/[^/]+$/, projects.items[0]],
    [/^\/projects\/[^/]+\/work-items$/, workItems],
    [/^\/projects\/[^/]+\/findings$/, findings],
    [/^\/projects\/[^/]+\/releases$/, releases],
    [/^\/projects\/[^/]+\/trains\/prod$/, trains.prod],
    [/^\/projects\/[^/]+\/trains\/staging$/, trains.staging],
    [/^\/projects\/[^/]+\/costs$/, costs],
    [/^\/projects\/[^/]+\/metrics\/dora$/, dora],
    [/^\/projects\/[^/]+\/workflow$/, workflow],
    [/^\/projects\/[^/]+\/workflows$/, workflowSummaries],
    [/^\/projects\/[^/]+\/workflows\/[^/]+\/versions$/, workflowVersions],
    [/^\/projects\/[^/]+\/workflows\/hotfix$/, hotfix],
    [/^\/projects\/[^/]+\/workflows\/release-full$/, releaseFull],
    [/^\/projects\/[^/]+\/workflows\/[^/]+$/, workflow],
    [/^\/projects\/[^/]+\/workflow-routing$/, workflowRouting],
    [/^\/workflows\/templates$/, [{ name: "default-simple", version: 1, display: "default-simple", description: "", yaml: workflow.yaml }]],
    [/^\/projects\/[^/]+\/memory\/search$/, memories],
    [/^\/projects\/[^/]+\/memory\/pending$/, pendingMemories],
    // Checkout Web livre par Jira, GitHub et Argo CD — dont la dernière vérification a échoué.
    [
      /^\/projects\/[^/]*checkout-web\/connectors$/,
      [
        { id: "c-jira", kind: "tracker", type: "jira", config: {}, secret_refs: {}, status: "ok" },
        { id: "c-gh", kind: "scm", type: "github", config: {}, secret_refs: {}, status: "ok" },
        { id: "c-argo", kind: "cd", type: "argocd", config: {}, secret_refs: {}, status: "error" },
      ],
    ],
    // Un tracker interne : la demande se pose dans la console (le board offre “ new request ”).
    [/^\/projects\/[^/]+\/connectors$/, [{ id: "c-tracker", kind: "tracker", type: "internal", config: {}, secret_refs: {}, status: "ok" }]],
    [/^\/work-items\/[^/]+\/timeline$/, timeline],
    [/^\/work-items\/[^/]+\/runs$/, runs],
    [/^\/runs\/[^/]+\/events$/, runEvents],
    [/^\/runs\/[^/]+\/access$/, runAccess],
    [/^\/runs\/[^/]+\/diff$/, runDiff],
    [/^\/runs\/[^/]+$/, runs[2]],
    [/^\/audit$/, { items: [], meta: { has_more: false } }],
  ];
  for (const [pattern, value] of table) {
    if (pattern.test(route ?? "")) return value as T;
  }
  return {} as T;
}
