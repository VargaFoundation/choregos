// SPDX-License-Identifier: Apache-2.0
/**
 * Fixtures du mode démo (`NEXT_PUBLIC_API_MODE=mock`).
 *
 * Elles racontent un projet vivant : un ticket en cours d'implémentation, un autre en
 * attente de validation, un train prêt à partir, un finding à trier. Le flux front
 * avance sans l'API — c'est ce que demande la section 3.4 du plan.
 */
import type {
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
  WorkflowRouting,
  WorkflowSummary,
  WorkflowValidation,
  WorkItemDto,
  WorkItemPage,
} from "@/lib/types";

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
    item("w1", "varga/billing-api#123", "Les avoirs ne sont pas déduits du total", "in_progress", "En cours", {
      size: "M" as const,
      cost: 6.77,
      run: { id: "r3", status: "running", stage_role: "implement", attempt: 1, cost_usd: 2.74, backend: "claude-code" },
    }),
    item("w2", "varga/billing-api#124", "Exporter les factures en PDF", "awaiting_spec_approval", "Spec à valider", {
      size: "L",
      cost: 1.12,
      pending: true,
    }),
    item("w3", "varga/billing-api#125", "Corriger l'arrondi des remises", "pr_open", "PR ouverte", {
      size: "S",
      cost: 3.4,
      pr: "https://github.com/varga/billing-api/pull/456",
    }),
    item("w4", "varga/billing-api#120", "Migrer vers pydantic v2", "deployed_prod", "En production", {
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
          payload: { summary: "Validation de la spécification demandée" },
          requested_at: iso(180),
        }
      : undefined,
    created_at: iso(60 * 20),
    closed_at: extra.closed ? iso(60 * 2) : undefined,
  };
}

export const timeline: TimelineEntry[] = [
  { ts: iso(240), kind: "state_change", title: "inbox → refining", actor: "orchestrateur", actor_kind: "system" },
  {
    ts: iso(235),
    kind: "run",
    title: "refine — tentative 1",
    detail: "spec rédigée, 6 chemins autorisés",
    actor: "claude-code",
    actor_kind: "agent",
    cost_usd: 0.34,
    ref_id: "r1",
  },
  { ts: iso(200), kind: "decision", title: "validation : approuvée", actor: "augustin", actor_kind: "user" },
  {
    ts: iso(90),
    kind: "run",
    title: "implement — tentative 1",
    detail: "7 commits, 412 tests verts",
    actor: "claude-code",
    actor_kind: "agent",
    cost_usd: 2.74,
    ref_id: "r3",
  },
  {
    ts: iso(80),
    kind: "finding",
    title: "finding medium : requête N+1 sur les lignes",
    detail: "src/orders/repository.py:88",
    actor_kind: "agent",
  },
];

export const runs: Run[] = [
  run("r1", "refine", "succeeded", 0.34, "spec rédigée"),
  run("r2", "verify", "succeeded", 0.62, "412 tests verts, couverture +1,2"),
  run("r3", "implement", "running", 2.74, "en cours"),
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

export const runEvents: RunEventDto[] = [
  { seq: 1, type: "run.started", ts: iso(60), payload: { role: "implement" } },
  {
    seq: 0,
    type: "gate.outcome",
    ts: iso(30),
    payload: { name: "scope_respected", passed: true, pending: false, detail: "2 fichiers, tous dans le périmètre" },
  },
  {
    seq: 0,
    type: "gate.outcome",
    ts: iso(30),
    payload: { name: "evidence_present", passed: false, pending: false, detail: "aucun test exécuté (tests_run absent)" },
  },
  { seq: 2, type: "session/update", ts: iso(59), payload: { text: "Je lis src/orders/total.py" } },
  {
    seq: 3,
    type: "session/request_permission",
    ts: iso(58),
    payload: { allowed: true, kind: "edit", target: "src/orders/total.py", reason: "dans le périmètre autorisé" },
  },
  {
    seq: 4,
    type: "session/request_permission",
    ts: iso(57),
    payload: {
      allowed: false,
      kind: "edit",
      target: "src/billing/rates.py",
      reason: "hors du périmètre autorisé — utilise report_finding ou request_scope_change",
    },
  },
  { seq: 5, type: "dod.retry", ts: iso(40), payload: { iteration: 1, failures: ["tests"] } },
  { seq: 6, type: "session/update", ts: iso(35), payload: { text: "Je corrige l'arrondi et relance les tests" } },
  { seq: 7, type: "run.result", ts: iso(30), payload: { status: "done", summary: "avoirs déduits du total" } },
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
      items: [{ work_item_key: "varga/billing-api#120", sha: "a1b2c3", title: "Migrer vers pydantic v2" }],
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
      verdict: { go: false, reason: "analyse canary KO : 5xx > 1 %" },
    },
  ],
  meta: { has_more: false },
};

export const findings: FindingPage = {
  items: [
    {
      id: "f1",
      project_slug: "billing-api",
      title: "Requête N+1 sur le chargement des lignes",
      type: "perf",
      severity: "medium",
      evidence: "src/orders/repository.py:88 — 1 + N requêtes pour 200 lignes",
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
      title: "Test instable : test_invoice_totals",
      type: "flaky-test",
      severity: "low",
      evidence: "échoue 1 fois sur 12 en CI",
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
    content: "Les totaux sont arrondis au centime à l'émission, jamais à l'affichage (ADR-0007).",
    status: "active",
    valid_from: iso(60 * 24 * 180),
    provenance: { source: "scm", ref: "docs/adr/0007-arrondis.md" },
  },
  {
    id: "m2",
    kind: "incident",
    subject: "incident:billing-api:2026-06-11",
    content: "Rollback après une régression sur les avoirs : calcul déplacé côté client.",
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
    content: "Les tests d'acceptation suivent la structure Given/When/Then.",
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
  deployment_frequency: { value: 0.77, unit: "par jour", level: "high", sample: 23 },
  lead_time: { value: 9.4, unit: "heures (médiane)", level: "elite", sample: 23 },
  change_failure_rate: { value: 0.087, unit: "part des mises en production", level: "high", sample: 23 },
  time_to_restore: { value: 1.6, unit: "heures (médiane)", level: "high", sample: 2 },
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
  inbox: { display: À trier, kind: wait }
  ready: { display: Prêt }
  done: { display: Fini, terminal: true }
transitions:
  - { id: t-refine, from: inbox, to: ready, by: refiner }
  - { id: t-implement, from: ready, to: done, by: dev, gates: [scope_respected] }
`,
};

/** Ce que l'API répond à la validation du workflow ci-dessus : la carte de `default-simple`, et sa
 * vue processus. Les arêtes `default` partent de chaque état d'agent : la carte les dit une fois. */
export const workflowValidation: WorkflowValidation = {
  valid: true,
  errors: [],
  warnings: [],
  graph: {
    nodes: [
      { id: "inbox", display: "À trier", kind: "wait", lane: "agent" },
      { id: "ready", display: "Prêt", kind: "normal", lane: "agent" },
      { id: "needs_human", display: "Besoin d'un humain", kind: "wait", lane: "human" },
      { id: "done", display: "Fini", kind: "normal", terminal: true, lane: "terminal" },
    ],
    edges: [
      { id: "t-refine", from: "inbox", to: "ready", kind: "nominal", label: "t-refine", actor: "refiner", gates: [] },
      { id: "t-implement", from: "ready", to: "done", kind: "nominal", label: "t-implement", actor: "dev", gates: ["scope_respected"] },
      { id: "inbox->needs_human:default:question", from: "inbox", to: "needs_human", kind: "default", label: "question" },
      { id: "ready->needs_human:default:question", from: "ready", to: "needs_human", kind: "default", label: "question" },
      { id: "ready->needs_human:default:budget", from: "ready", to: "needs_human", kind: "default", label: "budget dépassé" },
    ],
  },
  process: [
    {
      id: "t-refine",
      from: "inbox",
      from_display: "À trier",
      to: "ready",
      to_display: "Prêt",
      actor: "refiner",
      actor_type: "agent",
      who: "l'agent refiner (rôle refine)",
      outputs: ["spec_markdown"],
      gates: [],
      on_fail: null,
      on_reject: null,
      timeout_hours: null,
      sentence: "De « À trier » à « Prêt » : l'agent refiner (rôle refine) rédige la spec.",
    },
    {
      id: "t-implement",
      from: "ready",
      from_display: "Prêt",
      to: "done",
      to_display: "Fini",
      actor: "dev",
      actor_type: "agent",
      who: "l'agent dev (rôle implement)",
      outputs: [],
      gates: [{ name: "scope_respected", summary: "le diff reste dans le périmètre permis" }],
      on_fail: "réessaie deux fois, puis remonte à un humain",
      on_reject: null,
      timeout_hours: 72,
      sentence: "De « Prêt » à « Fini » : l'agent dev (rôle implement), si le diff reste dans le périmètre permis.",
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

/** Routeur des fixtures : reproduit les chemins de l'API réelle. */
const proposition = {
  proposal: "pr1",
  action_type: "open_infra_pr",
  status: "pending_approval",
  target: ["os-reboot-required"],
  params: { file: "platform/maintenance/os-reboot-required.yaml" },
  justification: "Deux nœuds attendent un redémarrage depuis une semaine : une PR planifie la fenêtre.",
  proposed_by: { kind: "user", id: "lea@varga.dev", via: "mcp" },
  approval: { approvers: [{ role: "owner", min: 1 }], step_up_minutes: 10, separation_of_duties: true },
  decisions: [],
  effects: [],
  evidence: [],
  created_at: iso(42),
  finished_at: null,
};

export async function mockApi<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  await new Promise((resolve) => setTimeout(resolve, 40));
  if (method === "POST" && path === "/workflows/validate") return workflowValidation as T;
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
  if (method !== "GET") return { ok: true } as T;
  const [route] = path.split("?");
  const table: Array<[RegExp, unknown]> = [
    [/^\/me$/, me],
    [/^\/edition$/, { edition: "community", features: [], version: "0.13.1" }],
    [
      /^\/integrations$/,
      { mcp_url: "http://localhost:3000/mcp", protocol_versions: ["2025-11-25", "2025-06-18"], oauth: { enabled: false }, version: "0.13.1" },
    ],
    [/^\/me\/tokens$/, []],
    [/^\/ui\/admin-sections$/, [sectionScim]],
    [/^\/orgs\/[^/]+\/scim\/settings$/, { enabled: true, default_role: "viewer" }],
    [/^\/orgs\/[^/]+\/scim\/tokens$/, { items: [{ id: "t1", name: "okta", created_at: iso(60 * 24 * 12) }] }],
    [/^\/orgs$/, [{ slug: "varga", name: "Varga Foundation", role: "org_admin" }]],
    [/^\/orgs\/[^/]+\/members$/, me.memberships],
    [/^\/templates$/, [{ name: "github-tekton-argo-k8s", version: "1.0.0", display: "GitHub · Tekton · Argo CD · Kubernetes", is_published: true }]],
    [/^\/connectors\/types$/, []],
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
    [/^\/projects\/[^/]+\/workflows\/[^/]+$/, workflow],
    [/^\/projects\/[^/]+\/workflow-routing$/, workflowRouting],
    [/^\/workflows\/templates$/, [{ name: "default-simple", version: 1, display: "default-simple", description: "", yaml: workflow.yaml }]],
    [/^\/projects\/[^/]+\/memory\/search$/, memories],
    [/^\/projects\/[^/]+\/memory\/pending$/, pendingMemories],
    // Un tracker interne : la demande se pose dans la console (le board offre « new request »).
    [/^\/projects\/[^/]+\/connectors$/, [{ id: "c-tracker", kind: "tracker", type: "internal", config: {}, enabled: true }]],
    [/^\/projects\/[^/]+\/proposals\/[^/]+$/, proposition],
    [/^\/projects\/[^/]+\/proposals$/, [proposition]],
    [/^\/work-items\/[^/]+\/timeline$/, timeline],
    [/^\/work-items\/[^/]+\/runs$/, runs],
    [/^\/work-items\/[^/]+$/, workItems.items[0]],
    [/^\/runs\/[^/]+\/events$/, runEvents],
    [/^\/runs\/[^/]+\/diff$/, { files: [], additions: 0, deletions: 0 }],
    [/^\/runs\/[^/]+$/, runs[2]],
    [/^\/audit$/, { items: [], meta: { has_more: false } }],
  ];
  for (const [pattern, value] of table) {
    if (pattern.test(route ?? "")) return value as T;
  }
  return {} as T;
}
