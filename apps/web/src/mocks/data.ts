// SPDX-License-Identifier: Apache-2.0
/**
 * Fixtures du mode démo (`NEXT_PUBLIC_API_MODE=mock`).
 *
 * Elles racontent un projet vivant : un ticket en cours d'implémentation, un autre en
 * attente de validation, un train prêt à partir, un finding à trier. Le flux front
 * avance sans l'API — c'est ce que demande la section 3.4 du plan.
 */
import devComplex from "./dev-complex.json";
import type {
  Action,
  Agent,
  AgentBackendInfo,
  AgentImageInfo,
  RemoteAgentDiscovery,
  RemoteCredential,
  GatewayModel,
  ModelCatalogue,
  UsableModel,
  AgentCatalogueEntry,
  AgentCreate,
  AgentFork,
  AgentScopeChange,
  AgentEvalCase,
  AgentEvalResult,
  AgentSpec,
  AgentTrialCreate,
  ConnectorType,
  McpCatalogue,
  McpCatalogueEntry,
  McpCatalogueInstall,
  OrgConnector,
  SkillCatalogue,
  SkillCatalogueEntry,
  ProjectOperation,
  ProjectRequirement,
  AgentCredential,
  AgentMetrics,
  AgentMetricsSummary,
  ImplicitAgent,
  ApiToken,
  ProjectAgent,
  ProjectAgentPut,
  BudgetAlert,
  KnowledgeBase,
  Skill,
  SkillGitPreview,
  SkillVersion,
  CostReport,
  DoraReport,
  FindingPage,
  MeDto,
  Membership,
  Memory,
  ProjectPage,
  ReleasePage,
  Run,
  RunEventDto,
  Artifact,
  RunLogs,
  HandOff,
  TimelineEntry,
  TrainStatus,
  WorkflowDef,
  WorkflowEditResult,
  WorkflowOperation,
  WorkflowRouting,
  WorkflowSummary,
  WorkflowValidation,
  WorkItemCreate,
  WorkItemDto,
  WorkItemPage,
} from "@/lib/types";
import { ApiError } from "@/lib/api";
import { parcoursDe, parcoursEnCours, parcoursTermine } from "./parcours";
import { peopleOps, peopleOpsApi, peopleOpsFixtures } from "./joiners-leavers";
import mcpCatalogueFile from "./mcp-catalogue.json";
import skillCatalogueFile from "./skill-catalogue.json";

export { peopleOpsFixtures };

const now = new Date();
const iso = (minutesAgo: number) => new Date(now.getTime() - minutesAgo * 60_000).toISOString();

const projectConfig = (slug: string) => ({
  slug,
  org: "acme",
  repo: { url: `https://github.com/acme/${slug}.git`, default_branch: "main", language: "python" as const },
});

export const me: MeDto = {
  id: "u1",
  email: "augustin@acme.dev",
  display_name: "Augustin",
  memberships: [
    { org: "acme", role: "org_admin", user_id: "u1", email: "augustin@acme.dev" },
  ],
};

/** The organisation's memberships: a person may have one for the organisation and one per project. */
export const memberships: Membership[] = [
  { org: "acme", role: "org_admin", user_id: "u1", email: "augustin@acme.dev" },
  { org: "acme", role: "developer", user_id: "u2", email: "lea@acme.dev" },
  { org: "acme", role: "project_owner", user_id: "u2", email: "lea@acme.dev", project_slug: "billing-api" },
  { org: "acme", role: "viewer", user_id: "u3", email: "marc@acme.dev" },
  { org: "acme", role: "developer", user_id: "u3", email: "marc@acme.dev", project_slug: "billing-api" },
  { org: "acme", role: "release_captain", user_id: "u3", email: "marc@acme.dev", project_slug: "checkout-web" },
];

export const projects: ProjectPage = {
  items: [
    {
      id: "p1",
      slug: "billing-api",
      org: "acme",
      name: "Billing API",
      status: "active",
      template_ref: "github-tekton-argo-k8s@1.0.0",
      config: projectConfig("billing-api"),
      workflow_name: "default-simple",
      policy_name: "solo",
      // Its workflows ship through the train (S30-07): the trains tab and counters show.
      has_release_train: true,
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
      org: "acme",
      name: "Checkout Web",
      status: "provisioning",
      config: projectConfig("checkout-web"),
      workflow_name: "advanced",
      policy_name: "team",
      has_release_train: true,
      stats: { active_work_items: 0, cost_month_eur: 0, trains_pending: 0 },
      created_at: iso(120),
      updated_at: iso(12),
    },
    peopleOps,
  ],
  meta: { has_more: false },
};

export const workItems: WorkItemPage = {
  items: [
    item("w1", "acme/billing-api#123", "Credit notes are not deducted from the total", "in_progress", "In progress", {
      size: "M" as const,
      cost: 6.77,
      run: { id: "r3", status: "running", stage_role: "implement", attempt: 1, cost_usd: 2.74, backend: "claude-code" },
    }),
    item("w2", "acme/billing-api#124", "Export invoices as PDF", "awaiting_spec_approval", "Spec to approve", {
      size: "L",
      cost: 1.12,
      pending: true,
    }),
    item("w3", "acme/billing-api#125", "Fix the rounding of discounts", "pr_open", "PR open", {
      size: "S",
      cost: 3.4,
      pr: "https://github.com/acme/billing-api/pull/456",
    }),
    item("w4", "acme/billing-api#120", "Migrate to pydantic v2", "deployed_prod", "In production", {
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
          due_at: iso(-60 * 21),
        }
      : undefined,
    created_at: iso(60 * 20),
    closed_at: extra.closed ? iso(60 * 2) : undefined,
  };
}

// ─── artifacts (ADR 0049): two revisions of the spec, a SARIF report, an image ───
const SPEC_V1 = "# Credit notes\n\nDeduct credit notes from the order total.\n";
const SPEC_V2 =
  "# Credit notes\n\nDeduct **credit notes** from the order total, then round half to even.\n\n## Acceptance\n\n- a credit note lowers the total\n- `total()` never goes negative\n";
const SARIF = JSON.stringify({
  version: "2.1.0",
  runs: [
    {
      tool: { driver: { name: "semgrep" } },
      results: [
        {
          ruleId: "python.lang.security.sql-injection",
          level: "error",
          message: { text: "SQL built from user input" },
          locations: [{ physicalLocation: { artifactLocation: { uri: "src/orders/repository.py" }, region: { startLine: 88 } } }],
        },
        {
          ruleId: "python.lang.best-practice.open-never-closed",
          level: "warning",
          message: { text: "file opened without a context manager" },
          locations: [{ physicalLocation: { artifactLocation: { uri: "src/orders/export.py" }, region: { startLine: 12 } } }],
        },
      ],
    },
  ],
});
/** A valid 1×1 PNG: what an image model behind the gateway would have produced. */
const PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";

const PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation";
/** A minimal one-page PDF. */
const PDF = [
  "%PDF-1.4",
  "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
  "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
  "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] >> endobj",
  "trailer << /Root 1 0 R >>",
  "%%EOF",
].join("\n");

const SPEC_V1_DIGEST = "sha256:9f2c4e1a7b3d5c8e0f6a2b4c6d8e0f1a3b5c7d9e1f3a5b7c9d1e3f5a7b9c1d3e";

function digestOf(seed: string): string {
  return `sha256:${seed.repeat(64).slice(0, 64)}`;
}

function artifactOf(
  name: string,
  version: number,
  type: Artifact["type"],
  kind: Artifact["kind"],
  media_type: string,
  size: number,
  run_id: string,
  stage: string,
  minutesAgo: number,
  agent?: [string, number],
): Artifact {
  return {
    name,
    type,
    kind,
    media_type,
    version,
    // The spec v1 carries the digest of its hand-off receipt: the receipt links to its readers.
    digest: name === "spec_markdown" && version === 1 ? SPEC_V1_DIGEST : digestOf(`${name.length}${version}`),
    size,
    produced_by: { run_id, stage, attempt: 1, agent: agent?.[0] ?? null, agent_version: agent?.[1] ?? null },
    storage_url: kind === "markdown" || kind === "json" ? null : `s3://choregos/orgs/varga/work-items/w1/artifacts/${name}`,
    created_at: iso(minutesAgo),
  };
}

export const artifacts: Artifact[] = [
  artifactOf("spec_markdown", 1, "markdown", "markdown", "text/markdown", SPEC_V1.length, "r1", "refine", 235, ["analyst", 3]),
  artifactOf("spec_markdown", 2, "markdown", "markdown", "text/markdown", SPEC_V2.length, "r2", "refine", 130, ["analyst", 3]),
  artifactOf("security_report", 1, "sarif", "report", "application/sarif+json", SARIF.length, "r3", "implement", 50),
  artifactOf("architecture.png", 1, "png", "file", "image/png", 68, "r3", "implement", 50),
  // Binary deliverables (S31-06): a deck from a skill, a report, a demo video from a gateway model.
  artifactOf("board_deck", 1, "pptx", "file", PPTX_TYPE, 4096, "r3", "implement", 40, ["presenter", 1]),
  artifactOf("quarterly_report", 1, "pdf", "file", "application/pdf", PDF.length, "r3", "implement", 40),
  artifactOf("demo", 1, "mp4", "file", "video/mp4", 2048, "r3", "implement", 40),
  // A revision whose stored bytes no longer match its digest: the API refuses it (409).
  artifactOf("budget_summary", 1, "markdown", "markdown", "text/markdown", 120, "r3", "implement", 45),
];

const ARTIFACT_CONTENT: Record<string, [string, () => BlobPart]> = {
  "spec_markdown@1": ["text/markdown", () => SPEC_V1],
  "spec_markdown@2": ["text/markdown", () => SPEC_V2],
  "security_report@1": ["application/sarif+json", () => SARIF],
  "architecture.png@1": ["image/png", () => Uint8Array.from(atob(PNG_B64), (c) => c.charCodeAt(0))],
  "board_deck@1": [PPTX_TYPE, () => new Uint8Array([0x50, 0x4b, 0x03, 0x04, ...new Array<number>(4092).fill(0)])],
  "quarterly_report@1": ["application/pdf", () => PDF],
  "demo@1": ["video/mp4", () => new Uint8Array(2048)],
};

/** The bytes of a revision in demo mode; `budget_summary` plays a revision altered behind the platform. */
export async function mockArtifactContent(_id: string, ref: string): Promise<Blob> {
  await new Promise((resolve) => setTimeout(resolve, 40));
  const [name, version] = ref.split("@");
  const latest = artifacts.filter((a) => a.name === name).at(-1);
  const key = version ? ref : `${name}@${latest?.version ?? 1}`;
  const entry = ARTIFACT_CONTENT[key];
  if (!entry) {
    const { ApiError } = await import("@/lib/api");
    throw new ApiError(409, {
      title: "Conflict",
      detail: `artifact \`${name}\` v${version ?? 1}: its content no longer matches its digest — it is not served`,
    });
  }
  return new Blob([entry[1]()], { type: entry[0] });
}

export const handOffs: HandOff[] = [
  {
    output: "spec_markdown",
    current_digest: "sha256:9f2c4e1a7b3d5c8e0f6a2b4c6d8e0f1a3b5c7d9e1f3a5b7c9d1e3f5a7b9c1d3e",
    events: [
      {
        kind: "produced",
        digest: "sha256:9f2c4e1a7b3d5c8e0f6a2b4c6d8e0f1a3b5c7d9e1f3a5b7c9d1e3f5a7b9c1d3e",
        run_id: "r1",
        stage: "refine",
        attempt: 1,
        at: iso(240),
      },
      {
        kind: "read",
        digest: "sha256:9f2c4e1a7b3d5c8e0f6a2b4c6d8e0f1a3b5c7d9e1f3a5b7c9d1e3f5a7b9c1d3e",
        run_id: "r3",
        stage: "implement",
        attempt: 1,
        at: iso(60),
      },
    ],
  },
  {
    output: "plan_markdown",
    current_digest: "sha256:4b8d2f6a0c4e8a2c6e0a4c8e2a6c0e4a8c2e6a0c4e8a2c6e0a4c8e2a6c0e4a8c",
    events: [
      {
        kind: "produced",
        digest: "sha256:4b8d2f6a0c4e8a2c6e0a4c8e2a6c0e4a8c2e6a0c4e8a2c6e0a4c8e2a6c0e4a8c",
        run_id: "r2",
        stage: "plan",
        attempt: 1,
        at: iso(120),
      },
      {
        kind: "read",
        digest: "sha256:4b8d2f6a0c4e8a2c6e0a4c8e2a6c0e4a8c2e6a0c4e8a2c6e0a4c8e2a6c0e4a8c",
        run_id: "r3",
        stage: "implement",
        attempt: 1,
        at: iso(60),
      },
    ],
  },
];

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
      // La couverture vient du récit de l'agent ; le reste, le runner l'a mesuré (ADR 0045).
      evidence: {
        tests_passed: true,
        tests_run: 412,
        tests_failed: 0,
        coverage_delta: 1.2,
        lint: "ok",
        measured: ["lint", "tests_failed", "tests_passed", "tests_run"],
      },
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

/** The pod log tail of the running run (`/runs/{id}/logs`, S36-03): a live snapshot, redacted. */
export const runLogsLive: RunLogs = {
  source: "live",
  state: "running",
  exit_code: null,
  reason: null,
  message: "",
  lines: [
    "runner: fetching the stage input",
    "runner: cloning https://github.com/varga/billing-api.git",
    "env: GITHUB_TOKEN=[REDACTED]",
    "acp: session started (claude-code)",
  ],
  truncated: false,
  error: null,
  captured_at: iso(1),
};

/** A run whose pod was OOM-killed before the agent spoke: only the archived log tail says why. */
export const runLogsOom: RunLogs = {
  source: "archive",
  state: "failed",
  exit_code: 137,
  reason: "OOMKilled",
  message: "runner exited with 137 (OOMKilled)",
  lines: ["runner: fetching the stage input", "agent: loading the embeddings index", "Killed"],
  truncated: true,
  error: null,
  captured_at: iso(5),
};

/** Ce à quoi l'agent a touché, replié depuis son journal (`/runs/{id}/access`). */
export const runAccess = {
  evenements: 64,
  refusals: 1,
  acces: [
    { nature: "read", target: "src/billing/totals.py", requests: 6, refusals: 0 },
    { nature: "write", target: "src/billing/totals.py", requests: 3, refusals: 0 },
    { nature: "write", target: "tests/billing/test_totals.py", requests: 2, refusals: 0 },
    { nature: "execute", target: "make test", requests: 4, refusals: 0 },
    {
      nature: "write",
      target: "src/billing/rates.py",
      requests: 1,
      refusals: 1,
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

/** Une mise à jour ACP telle que le runner la journalise : les `params` bruts de `session/update`. */
const maj = (update: Record<string, unknown>) => ({ sessionId: "sess-r3", update });
const tache = (content: string, status: string, priority = "medium") => ({ content, priority, status });

/** A run whose step a remote agent carried (S39-08): its task and its states, from the runner's journal. */
export const remoteRun: Run = { ...run("r-remote", "ask-rovo", "succeeded", 0.05, "Three open issues"), backend: "a2a", model: "a2a:rovo" };
/** The latest run of the remote agent: its card moved, nothing was sent. */
export const remoteRunDrifted: Run = {
  ...run("r-remote-drift", "ask-rovo", "failed", 0, "card changed"),
  backend: "a2a",
  model: "a2a:rovo",
  result: { schema: "choregos/StageResult/v1", status: "failed", reason: "remote_card_changed", summary: "card changed" } as Run["result"],
};
const remoteEvents = (drift: boolean): RunEventDto[] =>
  drift
    ? [
        { seq: 1, type: "run.started", ts: iso(40), payload: { role: "ask-rovo" } },
        {
          seq: 2,
          type: "remote.card_changed",
          ts: iso(40),
          payload: { pinned: rovoDiscovery.card.digest, now: `sha256:${"0a1b2c3d4e5f".repeat(5)}0a1b`.slice(0, 71), endpoint: "https://a2a.atlassian.com/v1/rovo" },
        },
      ]
    : [
        { seq: 1, type: "run.started", ts: iso(40), payload: { role: "ask-rovo" } },
        {
          seq: 2,
          type: "remote.started",
          ts: iso(40),
          payload: { endpoint: "https://a2a.atlassian.com/v1/rovo", skill: "work-in-jira", card_digest: rovoDiscovery.card.digest, protocol_version: "0.3.0" },
        },
        { seq: 3, type: "remote.update", ts: iso(39), payload: { state: "submitted", task_id: "task-7f3a" } },
        { seq: 4, type: "remote.update", ts: iso(39), payload: { state: "working", task_id: "task-7f3a" } },
        { seq: 5, type: "remote.update", ts: iso(38), payload: { state: "completed", task_id: "task-7f3a" } },
        { seq: 6, type: "remote.finished", ts: iso(38), payload: { state: "completed", task_id: "task-7f3a", duration_s: 74, reason: null } },
      ];

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
  // Le plan de l'agent, en trois révisions (S25-01) : la seconde coche, la troisième reformule une
  // tâche (« Add a regression test ») et en retire une autre (« Update the changelog »).
  {
    seq: 2,
    type: "session/update",
    ts: iso(59.9),
    payload: maj({
      sessionUpdate: "plan",
      entries: [
        tache("Read the totals code", "in_progress", "high"),
        tache("Deduct credit notes from the total", "pending", "high"),
        tache("Add a regression test", "pending"),
        tache("Update the changelog", "pending", "low"),
      ],
    }),
  },
  // Les appels d'outils (S25-02) : une lecture, un sous-agent, deux modifications — dont une refusée —,
  // puis les tests.
  {
    seq: 3,
    type: "session/update",
    ts: iso(59.8),
    payload: maj({
      sessionUpdate: "tool_call",
      toolCallId: "tc-1",
      title: "Read src/orders/total.py",
      kind: "read",
      status: "in_progress",
      locations: [{ path: "src/orders/total.py" }],
    }),
  },
  { seq: 4, type: "session/update", ts: iso(59.79), payload: maj({ sessionUpdate: "tool_call_update", toolCallId: "tc-1", status: "completed" }) },
  {
    seq: 5,
    type: "session/update",
    ts: iso(59.7),
    payload: maj({ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "Reading src/orders/total.py" } }),
  },
  {
    seq: 6,
    type: "session/update",
    ts: iso(59.6),
    payload: maj({ sessionUpdate: "tool_call", toolCallId: "tc-2", title: "Explore how credit notes are stored", kind: "think", name: "Task", status: "in_progress" }),
  },
  { seq: 7, type: "session/update", ts: iso(58.9), payload: maj({ sessionUpdate: "tool_call_update", toolCallId: "tc-2", status: "completed" }) },
  {
    seq: 8,
    type: "session/update",
    ts: iso(58.8),
    payload: maj({
      sessionUpdate: "tool_call",
      toolCallId: "tc-3",
      title: "Edit src/orders/total.py",
      kind: "edit",
      status: "pending",
      locations: [{ path: "src/orders/total.py" }],
    }),
  },
  {
    seq: 9,
    type: "session/request_permission",
    ts: iso(58.79),
    payload: {
      allowed: true,
      kind: "edit",
      target: "src/orders/total.py",
      reason: "within the allowed paths",
      params: { sessionId: "sess-r3", toolCall: { toolCallId: "tc-3" } },
    },
  },
  { seq: 10, type: "session/update", ts: iso(58.7), payload: maj({ sessionUpdate: "tool_call_update", toolCallId: "tc-3", status: "completed" }) },
  {
    seq: 11,
    type: "session/update",
    ts: iso(58.6),
    payload: maj({
      sessionUpdate: "plan",
      entries: [
        tache("Read the totals code", "completed", "high"),
        tache("Deduct credit notes from the total", "in_progress", "high"),
        tache("Add a regression test", "pending"),
        tache("Update the changelog", "pending", "low"),
      ],
    }),
  },
  {
    seq: 12,
    type: "session/update",
    ts: iso(57.5),
    payload: maj({
      sessionUpdate: "tool_call",
      toolCallId: "tc-4",
      title: "Edit src/billing/rates.py",
      kind: "edit",
      status: "pending",
      locations: [{ path: "src/billing/rates.py" }],
    }),
  },
  {
    seq: 13,
    type: "session/request_permission",
    ts: iso(57.49),
    payload: {
      allowed: false,
      kind: "edit",
      target: "src/billing/rates.py",
      reason: "outside the allowed paths — use report_finding or request_scope_change",
      params: { sessionId: "sess-r3", toolCall: { toolCallId: "tc-4" } },
    },
  },
  { seq: 14, type: "session/update", ts: iso(57.48), payload: maj({ sessionUpdate: "tool_call_update", toolCallId: "tc-4", status: "failed" }) },
  { seq: 15, type: "dod.retry", ts: iso(40), payload: { iteration: 1, failures: ["tests"] } },
  {
    seq: 16,
    type: "session/update",
    ts: iso(35),
    payload: maj({
      sessionUpdate: "agent_message_chunk",
      content: { type: "text", text: "Fixing the rounding and running the tests again" },
    }),
  },
  { seq: 17, type: "session/update", ts: iso(34.9), payload: maj({ sessionUpdate: "tool_call", toolCallId: "tc-5", title: "make test", kind: "execute", status: "in_progress" }) },
  { seq: 18, type: "session/update", ts: iso(34.3), payload: maj({ sessionUpdate: "tool_call_update", toolCallId: "tc-5", status: "completed" }) },
  {
    seq: 19,
    type: "session/update",
    ts: iso(34),
    payload: maj({
      sessionUpdate: "plan",
      entries: [
        tache("Read the totals code", "completed", "high"),
        tache("Deduct credit notes from the total", "completed", "high"),
        tache("Add regression tests for credit notes", "in_progress"),
      ],
    }),
  },
  { seq: 20, type: "run.result", ts: iso(30), payload: { status: "done", summary: "credit notes deducted from the total" } },
];

export const trains: Record<string, TrainStatus> = {
  prod: {
    env: "prod",
    status: "collecting",
    batch_size: 2,
    pending_items: ["acme/billing-api#125", "acme/billing-api#126"],
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
      items: [{ work_item_key: "acme/billing-api#120", sha: "a1b2c3", title: "Migrate to pydantic v2" }],
      started_at: iso(60 * 26),
      ended_at: iso(60 * 25),
      approved_by: "marie@acme.dev",
      verdict: { go: true },
    },
    {
      id: "rel2",
      project_slug: "billing-api",
      env: "prod",
      batch_no: 41,
      status: "rolled_back",
      items: [{ work_item_key: "acme/billing-api#118", sha: "d4e5f6" }],
      started_at: iso(60 * 50),
      ended_at: iso(60 * 49),
      verdict: { go: false, reason: "canary analysis failed: 5xx > 1%" },
    },
    {
      id: "rel3",
      project_slug: "billing-api",
      env: "prod",
      batch_no: 43,
      status: "awaiting_approval",
      items: [{ work_item_key: "acme/billing-api#124", sha: "b7c8d9", title: "Idempotent refund endpoint" }],
      started_at: iso(30),
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
      origin_work_item_key: "acme/billing-api#123",
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
      created_work_item_key: "acme/billing-api#127",
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

/**
 * The mock ledger (S29-03): the lines every grouping is computed from, so each grouping adds up to
 * the same total — as the API's do. Days are counted back from today, in UTC.
 */
type MockLine = { daysAgo: number; usd: number; kind: "model" | "tool"; model: string; agent: string | null; workflow: string; item: string; project: string };
const FX_EUR = 0.92;
export const mockLedger: MockLine[] = Array.from({ length: 45 }, (_, i) => i).flatMap((daysAgo) => [
  { daysAgo, usd: 1.5 + (daysAgo % 5) * 0.4, kind: "model" as const, model: "claude-sonnet", agent: "coordinator", workflow: "delivery", item: "BILL-12", project: "billing-api" },
  { daysAgo, usd: 0.8 + (daysAgo % 3) * 0.3, kind: "model" as const, model: "claude-haiku", agent: "reviewer", workflow: "delivery", item: "BILL-14", project: "billing-api" },
  ...(daysAgo % 4 === 0
    ? [{ daysAgo, usd: 0.25, kind: "tool" as const, model: "crm_export", agent: "coordinator", workflow: "onboarding", item: "HR-3", project: "checkout-web" }]
    : []),
  ...(daysAgo % 6 === 0
    ? [{ daysAgo, usd: 0.6, kind: "model" as const, model: "claude-sonnet", agent: null, workflow: "delivery", item: "BILL-12", project: "checkout-web" }]
    : []),
]);

export function mockCostReport(path: string): CostReport {
  const query = new URLSearchParams(path.split("?")[1] ?? "");
  const group = query.get("group_by") ?? "day";
  const project = /^\/projects\/([^/]+)\/costs/.exec(path.split("?")[0] ?? "")?.[1]?.split(":").at(-1);
  const since = query.get("since");
  const kind = query.get("kind");
  const day = (daysAgo: number) => new Date(now.getTime() - daysAgo * 86_400_000).toISOString().slice(0, 10);
  const key = (line: MockLine): string => {
    switch (group) {
      case "agent": return line.agent ?? "—";
      case "workflow": return line.workflow;
      case "work_item": return line.item;
      case "project": return line.project;
      case "model": return line.model;
      case "kind": return line.kind;
      default: return day(line.daysAgo);
    }
  };
  const rows = new Map<string, CostReport["rows"][number]>();
  for (const line of mockLedger) {
    if ((since && day(line.daysAgo) < since) || (kind && line.kind !== kind) || (project && line.project !== project)) continue;
    const k = key(line);
    const row = rows.get(k) ?? { key: k, cost_usd: 0, cost_eur: 0, tokens_in: 0, tokens_out: 0, tokens_cached: 0, runs: 0 };
    row.cost_usd = Math.round((row.cost_usd + line.usd) * 1e6) / 1e6;
    row.cost_eur = Math.round(row.cost_usd * FX_EUR * 1e6) / 1e6;
    row.runs = (row.runs ?? 0) + 1;
    rows.set(k, row);
  }
  const list = [...rows.values()].sort((a, b) => a.key.localeCompare(b.key));
  return {
    group_by: group,
    currency: "EUR",
    rows: list,
    total: {
      cost_usd: Math.round(list.reduce((s, r) => s + r.cost_usd, 0) * 1e6) / 1e6,
      cost_eur: Math.round(list.reduce((s, r) => s + r.cost_eur, 0) * 1e6) / 1e6,
      budget_usd: 150,
    },
  };
}

export const budgetAlerts: BudgetAlert[] = [
  { id: "ba1", scope: "project", subject: "billing-api", period: "day", period_key: now.toISOString().slice(0, 10), threshold: 80, spent_usd: 121.4, budget_usd: 150, status: "open", created_at: iso(35) },
  { id: "ba2", scope: "agent", subject: "onboarding-coordinator", period: "day", period_key: now.toISOString().slice(0, 10), threshold: 100, spent_usd: 20.3, budget_usd: 20, status: "open", created_at: iso(12) },
];

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
  { name: "default-simple", version: 1, is_default: true, open_items: 2, created_by: "lea@acme.dev", updated_at: iso(60 * 24 * 3) },
  { name: "hotfix", version: 2, is_default: false, open_items: 0, created_by: "marc@acme.dev", updated_at: iso(60 * 5) },
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
/**
 * The `dev-complex` template as a project's workflow (S39-13): the map and the process view are the
 * ones the API renders (`dev-complex.json`, written by `to_graph` and `to_process`). It is not in
 * `workflowSummaries`: other pages count the project's workflows.
 */
export const devComplexValidation = {
  valid: true,
  errors: [],
  warnings: [],
  graph: devComplex.graph,
  process: devComplex.process,
} as unknown as WorkflowValidation;
const devComplexDef: WorkflowDef = {
  ...workflow,
  name: "dev-complex",
  version: 1,
  is_default: false,
  yaml: "apiVersion: choregos/v1\nkind: Workflow\nmetadata: { name: dev-complex, version: 1 }\nstates: {}\n",
  json: { metadata: { name: "dev-complex", version: 1 }, initial: devComplex.initial, actors: {} } as unknown as WorkflowDef["json"],
};

/** The registry agents the demo organisation has installed: everything `dev-complex` names. */
const AGENTS_INSTALLES = new Set(
  (devComplex.process as { agent?: string | null }[]).map((s) => (s.agent ?? "").split("@")[0]).filter(Boolean),
);

/** `GET /projects/{id}/agent-resolution`: the installed ones resolve to v1, the others are in the catalogue. */
function resolutionDemo(path: string): unknown[] {
  const refs = new URLSearchParams(path.split("?")[1] ?? "").getAll("ref");
  return refs.map((ref) => {
    const slug = ref.split("@")[0]!;
    const titre = slug.replace(/-/g, " ").replace(/^./, (c) => c.toUpperCase());
    return AGENTS_INSTALLES.has(slug)
      ? { ref, slug, status: "resolved", version: Number(ref.split("@")[1] ?? 1), pinned: false, display_name: titre, lifecycle: "active", in_catalogue: true, reason: null }
      : { ref, slug, status: "not_installed", version: null, pinned: false, display_name: null, lifecycle: null, in_catalogue: true, reason: `agent \`${slug}\` does not exist in the project's organisation` };
  });
}

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
    created_by: "marc@acme.dev",
    created_at: iso(60 * 5),
    yaml: workflow.yaml.replace("gates: [scope_respected]", "gates: [scope_respected, ci_green]"),
  },
  { ...workflow, version: 1, is_active: false, created_by: "lea@acme.dev", created_at: iso(60 * 24 * 3) },
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
/** Atlassian's real card, summarised (S39-08): one agent, two skills, OAuth with Dynamic Client Registration. */
export const rovoDiscovery: RemoteAgentDiscovery = {
  card: {
    name: "Atlassian Rovo",
    description: "Chat with Rovo, Atlassian's AI teammate that helps you get things done across all your work tools.",
    endpoint: "https://a2a.atlassian.com/v1/rovo",
    host: "a2a.atlassian.com",
    protocol_version: "0.3.0",
    digest: `sha256:${"4f9c1e7a2b6d".repeat(5)}d8c3`.slice(0, 71),
    skills: [
      { id: "work-in-jira", name: "Work in Jira", description: "Get Jira context and update work items." },
      { id: "work-in-confluence", name: "Work in Confluence", description: "Find answers and create pages." },
    ],
    auth_types: ["oauth2_authorization_code"],
    scopes: ["read:me", "offline_access", "full_access:chat:rovo"],
    streaming: true,
    dcr_available: true,
  },
  proposed: {
    slug: "atlassian-rovo",
    kind: "remote",
    display_name: "Atlassian Rovo",
    description: "Chat with Rovo, Atlassian's AI teammate.",
    lifecycle: "draft",
    spec: { runtime: { kind: "a2a", card_url: "https://a2a.atlassian.com/.well-known/agent.json", credential: "atlassian-rovo" } },
  },
  credentials: ["atlassian-rovo"],
  domains: ["a2a.atlassian.com"],
};

export const remoteCredentials: RemoteCredential[] = [
  {
    name: "atlassian-rovo",
    type: "oauth2_authorization_code",
    status: "connected",
    authorised_as: "choregos-bot@acme.dev",
    scopes: ["read:me", "offline_access", "full_access:chat:rovo"],
    card_url: "https://a2a.atlassian.com/.well-known/agent.json",
    host: "a2a.atlassian.com",
    created_by: "lea@acme.dev",
    last_refresh_at: iso(25),
  },
  {
    name: "partner-agent",
    type: "oauth2_authorization_code",
    status: "pending",
    scopes: ["chat"],
    card_url: "https://agent.partner.example/.well-known/agent.json",
    host: "agent.partner.example",
    created_by: "lea@acme.dev",
  },
];

export const agents: Agent[] = [
  {
    slug: "rovo",
    kind: "remote",
    display_name: "Atlassian Rovo",
    description: "Answers from the team's Jira and Confluence, as the dedicated Atlassian account.",
    status: "active",
    owner: "lea@acme.dev",
    latest_version: 1,
    created_at: iso(60 * 24 * 3),
    versions: [
      {
        version: 1,
        spec: {
          instructions: "Answer about the work item, in two paragraphs at most.",
          runtime: {
            kind: "a2a",
            card_url: "https://a2a.atlassian.com/.well-known/agent.json",
            skill: "work-in-jira",
            credential: "atlassian-rovo",
            timeout_minutes: 10,
            declared_cost: { per_task_usd: 0.05, per_minute_usd: 0 },
            input: "text",
            pinned_card: {
              url: "https://a2a.atlassian.com/v1/rovo",
              protocol_version: "0.3.0",
              digest: rovoDiscovery.card.digest,
              skills: ["work-in-jira", "work-in-confluence"],
            },
          },
        },
        checksum: "sha256:77aa00bb11cc22dd33ee44ff55aa66bb",
        created_by: "lea@acme.dev",
        created_at: iso(60 * 24 * 3),
      },
    ],
  },
  {
    slug: "onboarding-coordinator",
    kind: "internal",
    display_name: "Onboarding coordinator",
    description: "Prepares a joiner's access plan and follows each step up to the badge.",
    status: "active",
    owner: "lea@acme.dev",
    latest_version: 2,
    scope: "org",
    can: { publish: true, manage: true, pin: true, fork_scopes: ["org", "project", "personal"] },
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
        created_by: "lea@acme.dev",
        created_at: iso(60 * 24 * 2),
      },
      {
        version: 1,
        spec: { instructions: "You prepare a joiner's access plan.", model: "profile:standard" },
        checksum: "sha256:1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d",
        created_by: "lea@acme.dev",
        created_at: iso(60 * 24 * 9),
      },
    ],
  },
  {
    slug: "leas-claude-code",
    kind: "external",
    display_name: "Léa's Claude Code",
    status: "active",
    owner: "lea@acme.dev",
    latest_version: 1,
    created_at: iso(60 * 24),
    versions: [
      {
        version: 1,
        spec: { mcp_servers: [{ connector: "choregos", tools: ["list_*", "search_*", "get_*", "create_work_item"] }] },
        checksum: "sha256:5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
        created_by: "lea@acme.dev",
        created_at: iso(60 * 24),
      },
    ],
  },
  // One agent per scope (S39-09, ADR 0061). The `can` and the reason are the server's: the console shows them as they are.
  {
    slug: "developer",
    kind: "internal",
    display_name: "Developer",
    description: "Implements the specification inside the allowed paths, test first.",
    status: "active",
    owner: null,
    latest_version: 2,
    scope: "platform",
    project: null,
    can: { publish: false, manage: false, pin: true, fork_scopes: ["project", "personal"] },
    cannot_reason: "This is a common agent of the platform's catalogue: only an organisation admin changes it. Fork it into your project to change a copy.",
    versions: [
      {
        version: 2,
        spec: { instructions: "Implement the specification, test first.\nSmall conventional commits.", model: "profile:standard" },
        checksum: "sha256:2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a",
        created_by: "catalogue:developer@2",
        created_at: iso(60 * 24 * 3),
      },
      {
        version: 1,
        spec: { instructions: "Implement the specification.", model: "profile:standard" },
        checksum: "sha256:1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b",
        created_by: "catalogue:developer@1",
        created_at: iso(60 * 24 * 20),
      },
    ],
  },
  {
    slug: "developer-billing-api",
    kind: "internal",
    display_name: "Developer (billing-api)",
    description: "The common developer, forked into billing-api.",
    status: "active",
    owner: "lea@acme.dev",
    latest_version: 1,
    scope: "project",
    project: "billing-api",
    forked_from: { slug: "developer", version: 1, scope: "platform", latest_version: 2, behind: true, available: true },
    can: { publish: true, manage: true, pin: true, fork_scopes: ["project", "personal"] },
    cannot_reason: null,
    versions: [
      {
        version: 1,
        spec: { instructions: "Implement the specification.", model: "profile:standard" },
        checksum: "sha256:1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b",
        created_by: "lea@acme.dev",
        created_at: iso(60 * 24 * 5),
      },
    ],
  },
  {
    slug: "leas-scratchpad",
    kind: "internal",
    display_name: "Lea's scratchpad",
    description: "An experiment of Lea's: it never runs in a workflow.",
    status: "active",
    owner: "lea@acme.dev",
    latest_version: 1,
    scope: "personal",
    project: null,
    can: { publish: true, manage: true, pin: false, fork_scopes: ["project", "personal"] },
    cannot_reason: null,
    versions: [
      {
        version: 1,
        spec: { instructions: "Try ideas.", model: "profile:cheap" },
        checksum: "sha256:3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c",
        created_by: "lea@acme.dev",
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
  entreeDuCatalogue("claude-code", "Claude Code", "external", "Claude Code acting through the MCP door, with its person's rights.", { kind: "external", client: "claude-code", reach: "always" }),
  entreeDuCatalogue("chatgpt", "ChatGPT", "external", "ChatGPT acting through the MCP door, with its person's rights.", {
    kind: "external", client: "chatgpt", reach: "cloud", offered: false, unavailable_reason: "the MCP door does not accept OAuth (`global.mcp.oauth`); the console's public address is not https",
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

/** The eval suite as the API plays it (S32-03), on a naive rendering: enough for the demo. */
function evalsOf(spec: AgentSpec, cases: AgentEvalCase[]): AgentEvalResult[] {
  return cases.map((c) => {
    const text = (spec.instructions ?? "")
      .replace(/\{\{\s*ticket\.title\s*\}\}/g, c.input?.title ?? "")
      .replace(/\{\{\s*ticket\.body\s*\}\}/g, c.input?.body ?? "");
    const missing = (c.must_contain ?? []).filter((m) => !text.includes(m));
    const forbidden = (c.must_not_contain ?? []).filter((f) => text.includes(f));
    return { name: c.name, passed: !missing.length && !forbidden.length, missing, forbidden, error: null };
  });
}

/** Refuse (409) a version that fails its suite: the published version's cases and its own. */
function evalGate(spec: AgentSpec, published?: AgentSpec): void {
  const failing = evalsOf(spec, [...(published?.evals ?? []), ...(spec.evals ?? [])]).filter((r) => !r.passed);
  if (failing.length) {
    throw new ApiError(409, {
      title: "Eval suite failed",
      detail: `${failing.length} case(s) of the agent's eval suite fail: the version is not published`,
      errors: failing.map((r) => ({ loc: ["evals", r.name], msg: (r.missing ?? []).map((m) => `missing “${m}”`).join("; ") })),
    });
  }
}

/** The trial run of the demo: it succeeds, for a few cents (S32-03). */
export const trialRun: Run = {
  ...runs[0]!,
  id: "trial-run-1",
  status: "succeeded",
  stage_role: "implement",
  transition_id: "trial",
  cost_usd: 0.04,
  result: { status: "done", summary: "Sorted: urgent — the checkout is down for every customer." } as Run["result"],
};

/** The gateway's models and the agent backends, for the builder's pickers (S32-01). */
export const platformModels: GatewayModel[] = [
  { model_name: "platform/strong", litellm_model: "anthropic/claude-opus-5", provider: "anthropic", input_cost_per_1k: 0.015, output_cost_per_1k: 0.075 },
  { model_name: "platform/standard", litellm_model: "anthropic/claude-sonnet-5", provider: "anthropic", input_cost_per_1k: 0.003, output_cost_per_1k: 0.015 },
  { model_name: "platform/cheap", litellm_model: "anthropic/claude-haiku-4-5", provider: "anthropic", input_cost_per_1k: 0.0008, output_cost_per_1k: 0.004 },
];

/**
 * The organisation's model catalogue (S39-07, ADR 0060), mutable for the length of the page: a model
 * disabled on `/admin/models` leaves the agent builder's picker, as it does against the API.
 */
export const modelCatalogue: ModelCatalogue = {
  mode: "open",
  gateway: { type: "openai-compatible", capabilities: ["list_models"] },
  tiers: { cheap: null, standard: null, strong: null, aux: null, embed: null, local: null },
  entries: platformModels.map((m, i) => ({
    id: `mc-${i + 1}`,
    model_name: m.model_name,
    display_name: m.model_name,
    source: "gateway" as const,
    enabled: true,
    allowed_roles: [],
    usages: ["agent" as const],
    price_override: null,
    gateway: {
      provider: m.provider,
      upstream_model: m.litellm_model,
      input_cost_per_1k: m.input_cost_per_1k,
      output_cost_per_1k: m.output_cost_per_1k,
      supports_tool_calling: true,
      supports_vision: false,
    },
    last_seen_at: iso(60),
    missing_since: null,
    notes: "",
    updated_by: null,
  })),
};

/** What an agent may be given: the enabled entries, for the role and usage asked. */
export function usableModelsOf(path: string): UsableModel[] {
  const query = new URLSearchParams(path.split("?")[1] ?? "");
  const role = query.get("role");
  const usage = query.get("usage") ?? "agent";
  return modelCatalogue.entries
    .filter((e) => e.enabled && (e.usages ?? []).includes(usage as "agent") && (!role || !(e.allowed_roles ?? []).length || (e.allowed_roles ?? []).includes(role)))
    .map((e) => ({
      model_name: e.model_name,
      display_name: e.display_name,
      provider: e.gateway?.provider ?? "",
      input_cost_per_1k: e.price_override?.input_per_1k ?? e.gateway?.input_cost_per_1k ?? null,
      output_cost_per_1k: e.price_override?.output_per_1k ?? e.gateway?.output_cost_per_1k ?? null,
      priced_by: e.price_override ? ("override" as const) : e.gateway?.input_cost_per_1k != null ? ("gateway" as const) : ("none" as const),
      tiers: [],
    }));
}

export const platformBackends: AgentBackendInfo[] = [
  { name: "claude-code", enabled: true, version: "2.1.0", capabilities: ["mcp", "skills"] },
  { name: "codex", enabled: false, capabilities: ["mcp"], disabled_reason: "conformance below threshold" },
];

/** The images an operator named for agents written in code: names only, never references (ADR 0063). */
export const platformAgentImages: AgentImageInfo[] = [
  { name: "ticket-triager", digest_pinned: true },
  { name: "scratch-bench", digest_pinned: false },
];

/** The memberships the API returns: the organisation's, or one project's with `?project=` (S30-06). */
function membersOf(path: string): Membership[] {
  const project = new URLSearchParams(path.split("?")[1] ?? "").get("project");
  return project ? memberships.filter((m) => m.project_slug === project) : memberships;
}

/** The registry's columns, one line per agent (S30-04). */
export function agentMetricsList(): AgentMetricsSummary[] {
  return agents.map((agent) =>
    agent.slug === agentMetrics.agent
      ? { agent: agent.slug, days: 30, runs: agentMetrics.runs, success_rate: agentMetrics.success_rate, cost_usd: agentMetrics.cost_usd }
      : { agent: agent.slug, days: 30, runs: 0, success_rate: null, cost_usd: 0 },
  );
}

/** The project's implicit agents, as the API computes them from its active workflows (S30-04). */
export function implicitAgents(): ImplicitAgent[] {
  const registry = new Set(agents.map((a) => a.slug));
  const found: ImplicitAgent[] = [];
  for (const def of [workflow, hotfix]) {
    const actors = ((def.json ?? {}) as { actors?: Record<string, Record<string, unknown>> }).actors ?? {};
    for (const [actor, spec] of Object.entries(actors)) {
      if (spec?.type !== "agent") continue;
      const named = String(spec.agent ?? "").split("@")[0];
      if (named && registry.has(named)) continue;
      found.push({
        workflow: def.name,
        actor,
        role: (spec.role as string | undefined) ?? null,
        model: (spec.model as string | undefined) ?? null,
        backend: (spec.backend as string | undefined) ?? null,
        max_turns: (spec.max_turns as number | undefined) ?? null,
        max_minutes: (spec.max_minutes as number | undefined) ?? null,
      });
    }
  }
  return found;
}

/** Le Claude Code de Léa a appelé la porte il y a trois minutes : la console le dit connecté. */
export const agentCredentials: AgentCredential[] = [
  {
    id: "cred-1",
    kind: "token",
    token_id: "tok-claude",
    token_name: "claude-code",
    created_by: "lea@acme.dev",
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
    risk: "medium",
    review_status: "approved",
    versions: [{ version: 1, digest: "sha256:7f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c", created_by: "lea@acme.dev", created_at: iso(60 * 24 * 3) }],
  },
];

export const knowledgeBase: KnowledgeBase = {
  slug: "architecture-framework",
  name: "Architecture framework",
  scope: "org",
  projects: [],
  groups: [],
  serving_mode: "files",
  status: "active",
  document_count: 1,
  documents_by_status: { ready: 1 },
  created_by: "lea@varga.dev",
  created_at: iso(60 * 24 * 5),
  documents: [
    {
      id: "kd-1",
      filename: "network-zoning.pptx",
      title: "Network zoning principles",
      mime: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      size_bytes: 29_339,
      digest: "sha256:1d2c3b4a",
      source: "upload",
      status: "ready",
      created_by: "lea@varga.dev",
      created_at: iso(60 * 24 * 5),
      markdown: "<!-- Slide number: 1 -->\n# Network zoning principles\nEvery zone has a firewall.\n",
    },
  ],
};

/** A base scoped to one project (S39-14): the runs of `billing-api` read it, nobody else's. */
export const projectKnowledgeBase: KnowledgeBase = {
  slug: "billing-runbooks",
  name: "Billing runbooks",
  scope: "projects",
  projects: ["billing-api"],
  groups: [],
  owner_project: "billing-api",
  serving_mode: "files",
  status: "active",
  document_count: 0,
  documents_by_status: {},
  created_by: "lea@varga.dev",
  created_at: iso(60 * 24 * 2),
  documents: [],
};

/** What `GET /orgs/{org}/knowledge` returns: the organisation's base, then the project's. */
export const knowledgeBases: KnowledgeBase[] = [knowledgeBase, projectKnowledgeBase];

export const skillVersion: SkillVersion = {
  version: 1,
  digest: "sha256:7f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c",
  created_by: "lea@acme.dev",
  created_at: iso(60 * 24 * 3),
  files: {
    "SKILL.md": "---\nname: onboarding-procedure\ndescription: The onboarding procedure.\n---\n\n# Onboarding\n\n1. Entra accounts ten days before the start date.\n2. Groups according to the access profile.\n",
    "profiles.md": "| role | groups |\n|---|---|\n| developer | devs, vpn |\n",
  },
  risk: "medium",
  review_status: "approved",
  findings: [{ code: "licence_missing", severity: "warning", detail: "no licence found: the right to use it is unknown" }],
};

/** A repository at a commit, as the preview of a git import shows it (ADR 0050). */
export const skillGitPreview: SkillGitPreview = {
  repo: "https://github.com/anthropics/skills",
  ref: "main",
  sha: "0123456789abcdef0123456789abcdef01234567",
  path: "skills",
  allowed: true,
  skills: [
    { path: "skills/brand-guidelines", name: "brand-guidelines", description: "Apply the brand colours and type.", files: 2, bytes: 4200, licence: "Apache-2.0", risk: "medium", findings: [] },
    {
      path: "skills/pdf",
      name: "pdf",
      description: "Read, fill and merge PDF files.",
      files: 12,
      bytes: 61000,
      licence: "LicenseRef-proprietary",
      risk: "high",
      findings: [
        { code: "script", severity: "warning", detail: "an executable script ships with the skill", file: "scripts/fill_form.py" },
        { code: "licence_not_open", severity: "warning", detail: "`LicenseRef-proprietary` is not an open-source licence" },
      ],
    },
  ],
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
  {
    kind: "documents",
    type: "sharepoint",
    display: "SharePoint / OneDrive",
    config_schema: objet({ tenant_id: { type: "string" }, client_id: { type: "string" } }, ["tenant_id", "client_id"]),
    capabilities: ["documents"],
    secret_fields: ["client_secret"],
  },
];

/**
 * The MCP catalogue (ADR 0065): the real shipped entries, generated into `mcp-catalogue.json` by
 * `make catalogues`, with the state this demo organisation gives them — an entry is installed once a
 * connector of the demo carries it.
 */
const mockProjectSlugs = ["billing-api", "checkout-web"];

export function mcpCatalogue(): McpCatalogue {
  const file = mcpCatalogueFile as unknown as McpCatalogue;
  return {
    ...file,
    entries: file.entries.map((entry: McpCatalogueEntry) => {
      const installed = orgConnectors.filter((c) => c.catalogue_entry?.startsWith(`${entry.id}@`));
      const selected = installed.some((c) => c.availability === "selected_projects");
      const base = {
        ...entry,
        can: {
          install: entry.install === "installable",
          configure_projects: [],
          enable_projects: selected ? mockProjectSlugs : [],
        },
        cannot_reason: entry.install === "installable" ? null : (entry.cannot_reason ?? null),
        enablement: installed.map((c) => ({
          connector: c.name,
          availability: c.availability ?? ("all_projects" as const),
          enabled_projects: c.enabled_projects ?? [],
        })),
      };
      return installed.length
        ? { ...base, state: "installed" as const, installed_as: installed.map((c) => c.name) }
        : base;
    }),
  };
}

/** The skills this demo organisation installed from the catalogue, by name (ADR 0065). */
const catalogueSkills = new Map<string, { skill: Skill; version: SkillVersion }>();

/**
 * The skills catalogue (ADR 0065): the real shipped entries, generated into `skill-catalogue.json` by
 * `make catalogues`, with the state this demo organisation gives them. A high-risk skill fetched from
 * git waits for a second administrator, as the API says.
 */
export function skillCatalogue(): SkillCatalogue {
  const file = skillCatalogueFile as unknown as SkillCatalogue;
  return {
    ...file,
    entries: file.entries.map((entry: SkillCatalogueEntry) => {
      const installed = catalogueSkills.get(entry.id);
      if (!installed) return entry;
      const waiting = installed.skill.review_status === "pending_review";
      return { ...entry, state: waiting ? ("pending_review" as const) : ("installed" as const), installed_version: 1 };
    }),
  };
}

/** Un annuaire de l'organisation (ADR 0034) : chaque opération porte sa politique. */
export const orgConnectors: OrgConnector[] = [
  {
    name: "entra-acme",
    kind: "identity",
    type: "entra",
    config: { tenant: "acme.onmicrosoft.com" },
    secret_refs: { client_secret: "env:CONNECTOR_ENTRA_CLIENT_SECRET" },
    // A reference an operator wrote: the console shows it as such (ADR 0058).
    secrets: [{ field: "client_secret", source: "reference", reference: "env:CONNECTOR_ENTRA_CLIENT_SECRET" }],
    status: "ok",
    created_by: "lea@acme.dev",
    availability: "all_projects",
    enabled_projects: [],
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
  secret_refs: { token: "store:connector/supplier-agent/token" },
  // A value typed in the console: the console only knows it is set, when and by whom.
  secrets: [{ field: "token", source: "store", version: 2, rotated_at: new Date(Date.now() - 3 * 86_400_000).toISOString(), rotated_by: "lea@acme.dev" }],
  status: "ok",
  availability: "all_projects",
  enabled_projects: [],
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
    decisions: [{ by: "lea@acme.dev", decision: "approve", at: iso(90), auth_age_seconds: 120 }],
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
  proposed_by: { kind: "user", id: "lea@acme.dev", via: "mcp" },
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

/**
 * The writes the demo received, in order: what a real API would have been sent. Playwright reads
 * them on `window.__mockWrites` to check a payload, since in demo mode no request leaves the page.
 */
function recordWrite(method: string, path: string, body: unknown): void {
  if (typeof window === "undefined") return;
  const target = window as unknown as { __mockWrites?: Array<{ method: string; path: string; body: unknown }> };
  (target.__mockWrites ??= []).push({ method, path, body });
}

/** The work items filed on Checkout Web (Jira) during this page's life, so the board shows them. */
const filedOnTheMock: WorkItemDto[] = [];

/** Routeur des fixtures : reproduit les chemins de l'API réelle. */
export async function mockApi<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  await new Promise((resolve) => setTimeout(resolve, 40));
  const body: unknown = typeof init.body === "string" ? JSON.parse(init.body) : undefined;
  if (method !== "GET") recordWrite(method, path, body);
  const hr = peopleOpsApi(method, path.split("?")[0] ?? path, body);
  if (hr !== undefined) return hr as T;
  const filed = /^\/projects\/([^/]+)\/work-items$/.exec(path.split("?")[0] ?? path);
  if (method === "POST" && filed) {
    // Filing on a project whose tracker is external: the issue is created there, the work item takes its key (ADR 0059).
    const corps = (body ?? {}) as WorkItemCreate;
    const jira = /checkout-web$/.test(decodeURIComponent(filed[1]!));
    const made = {
      ...item(`w-new-${filedOnTheMock.length + 1}`, jira ? `CHK-${101 + filedOnTheMock.length}` : `BILL-${900 + filedOnTheMock.length}`, corps.title, "inbox", "Inbox", { size: corps.size ?? undefined }),
      url: jira ? `https://jira.example/browse/CHK-${101 + filedOnTheMock.length}` : null,
    } as WorkItemDto;
    if (jira) filedOnTheMock.push(made);
    return made as T;
  }
  if (method === "GET" && /^\/projects\/[^/]*checkout-web\/work-items$/.test(path.split("?")[0] ?? path) && filedOnTheMock.length)
    return { items: filedOnTheMock, meta: { has_more: false } } as T;
  if (method === "POST" && path === "/workflows/validate") {
    // Comme l'API : un texte sans `states:` n'est pas un workflow, et ne se dessine pas.
    const texte = String((JSON.parse(String(init.body ?? "{}")) as { yaml?: string }).yaml ?? "");
    if (/name: release-full/.test(texte)) return releaseFullValidation as T;
    if (/name: dev-complex/.test(texte)) return devComplexValidation as T;
    if (!/^states:/m.test(texte)) {
      return { valid: false, errors: [{ code: "workflow.states", message: "a workflow declares its states", line: 1, column: 1 }], warnings: [] } as T;
    }
    return workflowValidation as T;
  }
  if (method === "POST" && /^\/orgs\/[^/]+\/knowledge$/.test(path)) {
    // The base created, as the body says it (its slug, scope and projects): the page opens it next.
    return { ...knowledgeBase, documents: [], document_count: 0, documents_by_status: {}, ...(body as object) } as T;
  }
  if (method === "POST" && /^\/projects\/[^/]+\/knowledge$/.test(path)) {
    // A base owned by the project: scoped to it by the API, not by the body.
    const slug = decodeURIComponent(path.split("/")[2] ?? "");
    return {
      ...knowledgeBase,
      documents: [],
      document_count: 0,
      documents_by_status: {},
      ...(body as object),
      scope: "projects",
      projects: [slug],
      owner_project: slug,
    } as T;
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
    // Publier crée la version suivante de celle qui a été lue (S16-02) ; créer un nom pris est refusé (#336).
    const corps = JSON.parse(String(init.body ?? "{}")) as { yaml: string; base_version?: number | null; create_only?: boolean };
    const nom = decodeURIComponent(path.split("/").at(-1)!);
    if (corps.create_only) {
      if (workflowSummaries.some((w) => w.name === nom))
        throw new ApiError(409, { detail: `\`${nom}\` already exists: open it to publish its next version, or choose another name` });
      workflowSummaries.push({ name: nom, version: 1, is_default: false, open_items: 0, created_by: me.email, updated_at: new Date().toISOString() });
      return { ...workflow, name: nom, yaml: corps.yaml, version: 1, is_default: false } as T;
    }
    return { ...workflow, yaml: corps.yaml, version: (corps.base_version ?? workflow.version) + 1 } as T;
  }
  const [chemin] = path.split("?");
  // Le routage et la désactivation changent l'état de la démo, le temps de la page : les écrans se
  // relisent comme sur la vraie API, et les mêmes refus s'y jouent (S23-13).
  if (method === "PUT" && /^\/projects\/[^/]+\/workflow-routing$/.test(chemin ?? "")) {
    const corps = JSON.parse(String(init.body ?? "{}")) as WorkflowRouting;
    const actifs = new Set(workflowSummaries.map((w) => w.name));
    const inconnus = [corps.default, ...corps.rules.map((r) => r.workflow)].filter((n) => !actifs.has(n));
    if (inconnus.length) throw new ApiError(422, { detail: `unknown or inactive workflow(s): ${inconnus.join(", ")}` });
    workflowRouting.default = corps.default;
    workflowRouting.rules = corps.rules;
    for (const w of workflowSummaries) w.is_default = w.name === corps.default;
    return workflowRouting as T;
  }
  const desactive = /^\/projects\/[^/]+\/workflows\/([^/]+)\/deactivate$/.exec(chemin ?? "");
  if (method === "POST" && desactive) {
    const nom = decodeURIComponent(desactive[1]!);
    if (nom === workflowRouting.default)
      throw new ApiError(409, { detail: `\`${nom}\` is the default workflow: choose another one before deactivating it` });
    if (workflowRouting.rules.some((r) => r.workflow === nom))
      throw new ApiError(409, { detail: `\`${nom}\` is the target of a routing rule: remove the rule first` });
    workflowSummaries.splice(workflowSummaries.findIndex((w) => w.name === nom), 1);
    return undefined as T;
  }
  const agentEcrit = /^\/orgs\/[^/]+\/agents\/([^/]+)(\/[a-z]+)?$/.exec(chemin ?? "");
  if (method === "POST" && /^\/orgs\/[^/]+\/agents$/.test(chemin ?? "")) {
    const corps = JSON.parse(String(init.body ?? "{}")) as AgentCreate;
    evalGate(corps.spec ?? {});
    const cree = {
      slug: corps.slug,
      kind: corps.kind ?? "internal",
      display_name: corps.display_name,
      description: corps.description ?? null,
      status: "active",
      lifecycle: corps.lifecycle ?? "active",
      owner: me.email,
      latest_version: 1,
      versions: [{ version: 1, spec: corps.spec ?? {}, checksum: "sha256:nouvelle", created_by: me.email }],
    } as Agent;
    // The agent joins the registry for the time of the page (S32-01): its page reads it back.
    if (!agents.some((a) => a.slug === cree.slug)) agents.push(cree);
    return structuredClone(cree) as T;
  }
  if (method === "POST" && /^\/orgs\/[^/]+\/agent-drafts$/.test(chemin ?? "")) {
    // A fixed proposal (S39-05): nothing is saved, as on the real endpoint.
    const corps = JSON.parse(String(init.body ?? "{}")) as { display_name: string; description: string };
    return {
      display_name: corps.display_name,
      slug: "release-notes-writer",
      description: corps.description,
      spec: {
        instructions: "You are a release notes writer.\n\n1. Read {{ ticket.title }}.\n2. List the merged pull requests.\n\n## Output\nThe notes, grouped by theme.",
        skills: [{ slug: "onboarding-procedure" }],
        limits: { max_turns: 20, max_minutes: 30 },
        budget: { run_usd: 1 },
        evals: [{ name: "groups by theme", input: { title: "Release 1.2", body: "" }, must_contain: ["theme"] }],
      },
      sample: { title: "Release notes for 1.2", body: "Merged: 14 pull requests since v1.1." },
      notes: ["dropped skill `ghost`: not in the organisation, or not usable yet"],
      model: "mock/drafter",
    } as T;
  }
  if (method === "POST" && agentEcrit?.[2] === "/fork") {
    const corps = JSON.parse(String(init.body ?? "{}")) as AgentFork;
    const source = agents.find((a) => a.slug === agentEcrit[1]) ?? agents[0]!;
    if (agents.some((a) => a.slug === corps.slug)) throw new ApiError(409, { detail: `\`${corps.slug}\` already exists: a fork takes a new slug` });
    const copied = source.versions?.find((v) => v.version === (corps.version ?? source.latest_version)) ?? source.versions![0]!;
    const fork = {
      slug: corps.slug,
      kind: source.kind,
      display_name: corps.display_name || `${source.display_name} (fork)`,
      description: source.description ?? null,
      status: "active",
      owner: me.email,
      latest_version: 1,
      scope: corps.scope,
      project: corps.scope === "project" ? (corps.project ?? null) : null,
      forked_from: { slug: source.slug, version: copied.version, scope: source.scope ?? "org", latest_version: source.latest_version, behind: copied.version < source.latest_version, available: true },
      can: { publish: true, manage: true, pin: corps.scope !== "personal", fork_scopes: ["org", "project", "personal"] },
      cannot_reason: null,
      versions: [{ version: 1, spec: copied.spec, checksum: copied.checksum, created_by: me.email }],
    } as Agent;
    agents.push(fork);
    return structuredClone(fork) as T;
  }
  if (method === "POST" && agentEcrit?.[2] === "/scope") {
    const corps = JSON.parse(String(init.body ?? "{}")) as AgentScopeChange;
    const agent = agents.find((a) => a.slug === agentEcrit[1]) ?? agents[0]!;
    agent.scope = corps.scope;
    agent.project = corps.scope === "project" ? (corps.project ?? null) : null;
    return structuredClone(agent) as T;
  }
  if (method === "POST" && /^\/orgs\/[^/]+\/agent-trials$/.test(chemin ?? "")) {
    const corps = JSON.parse(String(init.body ?? "{}")) as AgentTrialCreate;
    const publiee = agents.find((a) => a.slug === corps.agent);
    const budget = Math.min(0.5, corps.budget_usd ?? 0.25, corps.spec.budget?.run_usd ?? 0.5);
    return {
      run_id: trialRun.id,
      work_item_id: "trial-wi-1",
      project: corps.project,
      budget_usd: budget,
      status: "queued",
      evals: evalsOf(corps.spec, [...(publiee?.versions?.[0]?.spec.evals ?? []), ...(corps.spec.evals ?? [])]),
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
    // La version publiée s'ajoute à l'agent, le temps de la page : sa fiche la relit (S23-13).
    const agent = agents.find((a) => a.slug === agentEcrit[1]) ?? agents[0]!;
    const spec = JSON.parse(String(init.body ?? "{}")) as AgentSpec;
    evalGate(spec, agent.versions?.find((v) => v.version === agent.latest_version)?.spec);
    const version = { version: agent.latest_version + 1, spec, checksum: "sha256:suivante", created_by: me.email };
    agent.latest_version = version.version;
    agent.versions = [version, ...(agent.versions ?? [])];
    return version as T;
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
  const accuse = /^\/orgs\/[^/]+\/budget-alerts\/([^/]+)\/acknowledge$/.exec(chemin ?? "");
  if (method === "POST" && accuse) {
    const alerte = budgetAlerts.find((a) => a.id === accuse[1]) ?? budgetAlerts[0]!;
    return { ...alerte, status: "acknowledged", acknowledged_by: "lea@varga.dev" } as T;
  }
  if (method === "POST" && /^\/orgs\/[^/]+\/skills\/import$/.test(chemin ?? "")) return skills[0] as T;
  if (method === "POST" && /^\/orgs\/[^/]+\/skills\/import-git\/preview$/.test(chemin ?? "")) return skillGitPreview as T;
  if (method === "POST" && /^\/orgs\/[^/]+\/skills\/import-git$/.test(chemin ?? "")) return [skills[0]] as T;
  if (method === "POST" && /^\/orgs\/[^/]+\/skills\/[^/]+\/versions\/\d+\/approve$/.test(chemin ?? "")) return skillVersion as T;
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
  // Connector secrets (ADR 0058): the mock keeps what the console may know — set, version, who — and,
  // like the API, never a value.
  if (/^\/orgs\/[^/]+\/connectors(\/|$)/.test(chemin ?? "") && !/\/(discover|operations)/.test(chemin ?? "")) {
    const [, , , , nom, , champ] = (chemin ?? "").split("/");
    const corps = JSON.parse(String(init.body ?? "{}")) as {
      name?: string;
      kind?: string;
      type?: string;
      config?: Record<string, unknown>;
      secret_refs?: Record<string, string>;
      secret_values?: Record<string, string>;
    };
    const maintenant = new Date().toISOString();
    const etat = (field: string, version = 1): NonNullable<OrgConnector["secrets"]>[number] => ({
      field,
      source: "store",
      version,
      rotated_at: maintenant,
      rotated_by: me.email,
    });
    if (method === "POST" && !nom) {
      const cree: OrgConnector = {
        name: corps.name ?? "new",
        kind: corps.kind ?? "mcp",
        type: corps.type ?? "mcp",
        config: corps.config ?? {},
        secret_refs: {
          ...(corps.secret_refs ?? {}),
          ...Object.fromEntries(Object.keys(corps.secret_values ?? {}).map((f) => [f, `store:connector/${corps.name}/${f}`])),
        },
        secrets: [
          ...Object.keys(corps.secret_values ?? {}).map((f) => etat(f)),
          ...Object.entries(corps.secret_refs ?? {}).map(([f, r]) => ({ field: f, source: "reference" as const, reference: r })),
        ],
        status: "unknown",
        operations: [],
      };
      orgConnectors.push(cree);
      return cree as T;
    }
    const cible = orgConnectors.find((c) => c.name === nom);
    if (cible && method === "PUT" && champ) {
      const avant = cible.secrets?.find((s) => s.field === champ);
      cible.secrets = [...(cible.secrets ?? []).filter((s) => s.field !== champ), etat(champ, (avant?.version ?? 0) + 1)];
      return structuredClone(cible) as T;
    }
    if (cible && method === "DELETE" && champ) {
      cible.secrets = (cible.secrets ?? []).filter((s) => s.field !== champ);
      return structuredClone(cible) as T;
    }
    if (cible && method === "PATCH") {
      cible.config = corps.config ?? cible.config;
      return structuredClone(cible) as T;
    }
  }
  const skillInstall = /^\/orgs\/[^/]+\/skill-catalogue\/([^/]+)\/install$/.exec(chemin ?? "");
  if (method === "POST" && skillInstall) {
    // As the API does: the skill is published at version 1 with where it came from, and a high-risk
    // one fetched from git waits for a second administrator.
    const entry = skillCatalogue().entries.find((e) => e.id === skillInstall[1]);
    if (!entry) throw new ApiError(404, { title: "Not found" });
    const waiting = entry.risk === "high" && entry.delivery === "git";
    const source = {
      repo: entry.repo,
      sha: entry.sha,
      path: entry.path,
      licence: entry.licence,
      catalogue: `${entry.id}@${skillCatalogueFile.meta.catalogue_version}`,
      delivery: entry.delivery,
    };
    const version: SkillVersion = {
      version: 1,
      digest: entry.digest,
      created_by: me.email,
      created_at: new Date().toISOString(),
      files: { "SKILL.md": `---\nname: ${entry.id}\ndescription: ${entry.description}\n---\n` },
      source,
      risk: entry.risk,
      review_status: waiting ? "pending_review" : "approved",
      reviewed_by: waiting ? undefined : "platform",
      findings: [],
    };
    const skill: Skill = {
      slug: entry.id,
      description: entry.description,
      status: "active",
      latest_version: 1,
      used_by: [],
      risk: entry.risk,
      review_status: version.review_status,
      versions: [version],
    };
    catalogueSkills.set(entry.id, { skill, version });
    return skill as T;
  }
  const catalogueInstall = /^\/orgs\/[^/]+\/mcp-catalogue\/([^/]+)\/install$/.exec(chemin ?? "");
  if (method === "POST" && catalogueInstall) {
    // As the API does: a connector declared with the entry's URL, no operation until a discovery.
    const entry = mcpCatalogue().entries.find((e) => e.id === catalogueInstall[1]);
    const corps = JSON.parse(String(init.body ?? "{}")) as McpCatalogueInstall;
    if (!entry || entry.install !== "installable") throw new ApiError(422, { title: "Unprocessable", detail: "not installable" });
    const connector: OrgConnector = {
      name: corps.name || entry.id,
      kind: "mcp",
      type: "mcp",
      config: { url: entry.remote?.url },
      secret_refs: corps.secret_refs ?? {},
      status: "unknown",
      created_by: me.email,
      catalogue_entry: `${entry.id}@${mcpCatalogueFile.meta.catalogue_version}`,
      availability: corps.availability ?? "all_projects",
      enabled_projects: [],
      operations: [],
    };
    orgConnectors.push(connector);
    return connector as T;
  }
  if (method === "POST" && /^\/orgs\/[^/]+\/connectors\/[^/]+\/discover$/.test(chemin ?? "")) {
    return { added: ["annuler_commande"], changed: ["order_laptop"], removed: [], unchanged: 1 } as T;
  }
  const catalogueEcrit = /^\/orgs\/[^/]+\/models(?:\/([^/?]+))?$/.exec(chemin ?? "");
  if (method === "PUT" && catalogueEcrit?.[1] === "settings") {
    const corps = JSON.parse(String(init.body ?? "{}")) as { mode: "open" | "curated"; tiers?: ModelCatalogue["tiers"] };
    modelCatalogue.mode = corps.mode;
    modelCatalogue.tiers = { ...modelCatalogue.tiers, ...(corps.tiers ?? {}) };
    return { mode: modelCatalogue.mode, tiers: modelCatalogue.tiers } as T;
  }
  if (method === "POST" && catalogueEcrit?.[1] === "sync") return { added: 0, missing: 0, seen: modelCatalogue.entries.length } as T;
  if (method === "POST" && catalogueEcrit && !catalogueEcrit[1]) {
    const corps = JSON.parse(String(init.body ?? "{}")) as { model_name: string };
    const entree: ModelCatalogue["entries"][number] = {
      id: `mc-${modelCatalogue.entries.length + 1}`,
      model_name: corps.model_name,
      display_name: corps.model_name,
      source: "manual",
      enabled: true,
      allowed_roles: [],
      usages: ["agent"],
      price_override: null,
      gateway: null,
      last_seen_at: null,
      missing_since: null,
      notes: "",
      updated_by: me.email,
    };
    modelCatalogue.entries.push(entree);
    return entree as T;
  }
  if (method === "PATCH" && catalogueEcrit?.[1]) {
    const entree = modelCatalogue.entries.find((e) => e.id === catalogueEcrit[1]) ?? modelCatalogue.entries[0]!;
    const corps = JSON.parse(String(init.body ?? "{}")) as { clear_price_override?: boolean } & Partial<ModelCatalogue["entries"][number]>;
    const { clear_price_override: effacer, ...champs } = corps;
    Object.assign(entree, champs);
    if (effacer) entree.price_override = null;
    return entree as T;
  }
  if (method === "DELETE" && catalogueEcrit?.[1]) {
    modelCatalogue.entries = modelCatalogue.entries.filter((e) => e.id !== catalogueEcrit[1]);
    return undefined as T;
  }
  if (method === "POST" && /^\/orgs\/[^/]+\/agents\/discover$/.test(chemin ?? "")) return rovoDiscovery as T;
  if (method === "POST" && /^\/orgs\/[^/]+\/remote-credentials$/.test(chemin ?? "")) {
    const corps = JSON.parse(String(init.body ?? "{}")) as { name: string; type: RemoteCredential["type"]; card_url?: string };
    return { name: corps.name, type: corps.type, status: corps.type === "oauth2_authorization_code" ? "pending" : "connected", card_url: corps.card_url ?? null, host: null, scopes: [] } as T;
  }
  const autorise = /^\/orgs\/[^/]+\/remote-credentials\/([^/]+)\/authorize$/.exec(chemin ?? "");
  if (method === "POST" && autorise) return { redirect_url: `/admin/remote-agents?connected=${autorise[1]}` } as T;
  if (method !== "GET") return { ok: true } as T;
  const [route] = path.split("?");
  if (/^\/orgs\/[^/]+\/models$/.test(route ?? "")) return modelCatalogue as T;
  if (/^\/orgs\/[^/]+\/models\/usable$/.test(route ?? "")) return usableModelsOf(path) as T;
  if (/^\/orgs\/[^/]+\/models\/curate-preview$/.test(route ?? "")) {
    return { refused: [{ kind: "agent", ref: agents[0]?.slug ?? "an-agent", model: "platform/strong" }] } as T;
  }
  const installedVersion = /^\/orgs\/[^/]+\/skills\/([^/]+)\/versions\/1$/.exec(route ?? "");
  if (installedVersion && catalogueSkills.has(installedVersion[1]!)) return catalogueSkills.get(installedVersion[1]!)!.version as T;
  const installedSkill = /^\/orgs\/[^/]+\/skills\/([^/]+)$/.exec(route ?? "");
  if (installedSkill && catalogueSkills.has(installedSkill[1]!)) return catalogueSkills.get(installedSkill[1]!)!.skill as T;
  const actionLue = /^\/projects\/[^/]+\/actions\/([^/]+)$/.exec(route ?? "");
  if (actionLue) return (actions.find((a) => a.id === actionLue[1]) ?? actions[0]) as T;
  if (/^\/orgs\/[^/]+\/actions$/.test(route ?? "")) {
    const statut = new URLSearchParams(path.split("?")[1] ?? "").get("status");
    return actions.filter((a) => !statut || a.status === statut) as T;
  }
  // A copy: the mock keeps connectors across writes (ADR 0058), and a list returned as the same array
  // would be seen by the query cache as unchanged.
  if (/^\/orgs\/[^/]+\/connectors$/.test(route ?? "")) return structuredClone(orgConnectors) as T;
  const agentLu = /^\/orgs\/[^/]+\/agents\/([^/]+)$/.exec(route ?? "");
  // Une copie : l'agent change le temps de la page (S23-13), et une lecture doit le voir comme neuf.
  if (agentLu) return structuredClone(agents.find((a) => a.slug === agentLu[1]) ?? agents[0]) as T;
  // Le parcours d'un ticket et son détail, par identifiant : chacun le sien (S22-02).
  const parcours = /^\/work-items\/([^/]+)\/journey$/.exec(route ?? "");
  if (parcours) return parcoursDe(parcours[1]!) as T;
  const ticket = /^\/work-items\/([^/]+)$/.exec(route ?? "");
  if (ticket) return ticketDetaille(ticket[1]!) as T;
  if (/^\/orgs\/[^/]+\/agent-credentials$/.test(route ?? "")) {
    return agentCredentials.map((c) => ({ ...c, agent: "leas-claude-code" })) as T;
  }
  if (/^\/orgs\/[^/]+\/agent-metrics$/.test(route ?? "")) return agentMetricsList() as T;
  if (/^\/orgs\/[^/]+\/agents\/rovo\/runs$/.test(route ?? "")) return [remoteRunDrifted, remoteRun] as T;
  if (/^\/orgs\/[^/]+\/remote-credentials$/.test(route ?? "")) return remoteCredentials as T;
  if (/^\/runs\/r-remote\/events$/.test(route ?? "")) return remoteEvents(false) as T;
  if (/^\/runs\/r-remote-drift\/events$/.test(route ?? "")) return remoteEvents(true) as T;
  if (/^\/runs\/r-remote(-drift)?$/.test(route ?? "")) return (route?.endsWith("drift") ? remoteRunDrifted : remoteRun) as T;
  // The runs of an agent, newest first (S30-05).
  if (/^\/orgs\/[^/]+\/agents\/[^/]+\/runs$/.test(route ?? "")) return [...runs].reverse() as T;
  if (/^\/projects\/[^/]+\/implicit-agents$/.test(route ?? "")) return implicitAgents() as T;
  if (/^\/projects\/[^/]+\/agent-resolution$/.test(route ?? "")) return resolutionDemo(path) as T;
  const credentials = /^\/orgs\/[^/]+\/agents\/([^/]+)\/credentials$/.exec(route ?? "");
  if (credentials) return (credentials[1] === "leas-claude-code" ? agentCredentials : []) as T;
  if (method === "GET" && /^\/(orgs|projects)\/[^/]+\/costs$/.test(chemin ?? "") && path.includes("group_by=")) {
    // The project overview's own two reads (`day`, `kind`, no period) keep their fixed fixture.
    const surLaPage = /^\/projects\//.test(chemin ?? "") && !/since=|group_by=(?!day&|day$|kind)/.test(path);
    if (!surLaPage) return mockCostReport(path) as T;
  }
  const table: Array<[RegExp, unknown]> = [
    [/^\/me$/, me],
    [/^\/edition$/, { edition: "community", features: [], version: "0.13.1", publisher: null, edition_version: null }],
    [
      /^\/integrations$/,
      { mcp_url: "http://localhost:3000/mcp", protocol_versions: ["2025-11-25", "2025-06-18"], oauth: { enabled: false }, version: "0.13.1" },
    ],
    [/^\/me\/tokens$/, myTokens],
    [/^\/orgs\/[^/]+\/agents$/, agents],
    [/^\/orgs\/[^/]+\/agent-catalogue$/, agentCatalogue],
    [/^\/orgs\/[^/]+\/agents\/[^/]+\/metrics$/, agentMetrics],
    [/^\/projects\/[^/]+\/agents$/, projectAgents],
    [/^\/orgs\/[^/]+\/skills$/, [...skills, ...[...catalogueSkills.values()].map((c) => c.skill)]],
    [/^\/orgs\/[^/]+\/skill-catalogue$/, skillCatalogue()],
    [/^\/orgs\/[^/]+\/knowledge$/, knowledgeBases.map((b) => ({ ...b, documents: null }))],
    [/^\/orgs\/[^/]+\/knowledge\/[^/]+\/documents\/[^/]+$/, knowledgeBase.documents![0]],
    [/^\/orgs\/[^/]+\/knowledge\/billing-runbooks$/, projectKnowledgeBase],
    [/^\/orgs\/[^/]+\/knowledge\/[^/]+$/, { ...knowledgeBase, documents: knowledgeBase.documents!.map((d) => ({ ...d, markdown: null })) }],
    [/^\/orgs\/[^/]+\/budget-alerts$/, budgetAlerts],
    [/^\/orgs\/[^/]+\/skills\/[^/]+\/versions\/\d+$/, skillVersion],
    [/^\/orgs\/[^/]+\/skills\/[^/]+$/, skills[0]],
    [/^\/ui\/admin-sections$/, [sectionScim]],
    [/^\/orgs\/[^/]+\/scim\/settings$/, { enabled: true, default_role: "viewer" }],
    [/^\/orgs\/[^/]+\/scim\/tokens$/, { items: [{ id: "t1", name: "okta", created_at: iso(60 * 24 * 12) }] }],
    [/^\/orgs$/, [{ slug: "acme", name: "Varga Foundation", role: "org_admin", currency: "EUR" }]],
    [/^\/orgs\/[^/]+\/members$/, membersOf(path)],
    [/^\/templates$/, [{ name: "github-tekton-argo-k8s", version: "1.0.0", display: "GitHub · Tekton · Argo CD · Kubernetes", is_published: true }]],
    [/^\/connectors\/types$/, connectorTypes],
    [/^\/projects\/[^/]+\/requirements$/, projectRequirements],
    [/^\/orgs\/[^/]+\/connectors$/, orgConnectors],
    [/^\/orgs\/[^/]+\/mcp-catalogue$/, mcpCatalogue()],
    [/^\/projects\/[^/]+\/actions$/, actions],
    [/^\/projects\/[^/]+\/operations$/, projectOperations],
    [/^\/platform\/models$/, platformModels],
    [/^\/platform\/backends$/, platformBackends],
    [/^\/platform\/agent-images$/, platformAgentImages],
    [/^\/platform\/executors$/, []],
    [/^\/projects\/[^/]+\/models$/, { profiles: {}, allow_unvalidated: false, inherited: {} }],
    [/^\/projects\/[^/]+\/provision$/, { project_id: "p1", status: "succeeded", steps: [] }],
    [
      /^\/projects\/[^/]+\/policy$/,
      {
        name: "solo",
        version: 1,
        yaml: "budgets:\n  per_ticket_usd: 25\nrelease_train:\n  prod:\n    mode: train\n  staging:\n    mode: train\n",
        // the trains page lists these environments (S26-05)
        json: {
          apiVersion: "choregos/v1",
          kind: "Policy",
          metadata: { name: "solo", version: 1 },
          budgets: { per_ticket_usd: 25 },
          release_train: { prod: { mode: "train" }, staging: { mode: "train" } },
        },
        is_active: true,
      },
    ],
    [/^\/orgs\/[^/]+\/projects$/, projects],
    [/^\/projects\/[^/]*checkout-web$/, projects.items[1]],
    [/^\/projects\/[^/]+$/, projects.items[0]],
    // Checkout Web se provisionne : pas encore de ticket — la boîte ne compte pas deux fois ceux de Billing API.
    [/^\/projects\/[^/]*checkout-web\/work-items$/, { items: [], meta: { has_more: false } }],
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
    [/^\/projects\/[^/]+\/workflows\/dev-complex$/, devComplexDef],
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
    [/^\/work-items\/[^/]+\/hand-offs$/, handOffs],
    [/^\/work-items\/[^/]+\/artifacts$/, artifacts],
    [/^\/work-items\/[^/]+\/artifacts$/, artifacts],
    [/^\/work-items\/[^/]+\/runs$/, runs],
    [/^\/runs\/[^/]+\/events$/, runEvents],
    [/^\/runs\/r-oom\/logs$/, runLogsOom],
    [/^\/runs\/[^/]+\/logs$/, runLogsLive],
    [/^\/runs\/[^/]+\/access$/, runAccess],
    [/^\/runs\/[^/]+\/diff$/, runDiff],
    [/^\/runs\/trial-run-1$/, trialRun],
    [/^\/runs\/[^/]+$/, runs[2]],
    [/^\/audit$/, { items: [], meta: { has_more: false } }],
  ];
  for (const [pattern, value] of table) {
    if (pattern.test(route ?? "")) return value as T;
  }
  return {} as T;
}
