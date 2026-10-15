// SPDX-License-Identifier: Apache-2.0
/**
 * An HR project born from the `joiners-leavers` template, for the demo mode (S30-08): no repository,
 * no CI, no release train — an onboarding asks for its inputs when the ticket is filed, and the badge
 * is a task whose form and attestation travel with the decision.
 *
 * `joiners-leavers.json` is what the API returns for the template's onboarding workflow: the YAML
 * (its comments dropped), the part of the parsed document the console reads (`WorkflowDef.json`:
 * inputs and actors), the `task` of `t-hand-over-badge`, and `POST /workflows/validate` (its graph; the
 * process view emptied). It was written by running the API's `validate` route and
 * `choregos_core.parse_workflow` over `templates/joiners-leavers/workflows/onboarding.yaml`. The
 * offboarding workflow and the process view are left out: every fixture counts in the bundle budget.
 */
import type {
  JourneyMove,
  JourneyStep,
  ProjectDto,
  WorkflowDef,
  WorkflowSummary,
  WorkflowValidation,
  WorkItemCreate,
  WorkItemDto,
  WorkItemJourney,
} from "@/lib/types";
import template from "./joiners-leavers.json";

type Workflow = {
  yaml: string;
  json: Record<string, unknown>;
  badge_task: Record<string, unknown>;
  validation: WorkflowValidation;
};
const ONBOARDING = (template as unknown as { onboarding: Workflow }).onboarding;

const SLUG = "people-ops";
const now = Date.now();
const iso = (minutesAgo: number) => new Date(now - minutesAgo * 60_000).toISOString();

export const peopleOps: ProjectDto = {
  id: "p3",
  slug: SLUG,
  org: "varga",
  name: "People Ops",
  status: "active",
  template_ref: "joiners-leavers@2.0.0",
  config: { slug: SLUG, org: "varga" },
  workflow_name: "onboarding",
  policy_name: "solo",
  // No workflow of the template takes a release train: no trains tab, no trains counter (S30-07).
  has_release_train: false,
  stats: { active_work_items: 1, cost_month_eur: 3.1, trains_pending: 0 },
  created_at: iso(60 * 24 * 9),
  updated_at: iso(50),
};

export const peopleOpsWorkflows: WorkflowSummary[] = [
  {
    name: "onboarding",
    version: 1,
    description: String((ONBOARDING.json.metadata as { description?: string }).description ?? ""),
    is_default: true,
    open_items: 1,
    created_by: "template:joiners-leavers",
  },
];

function onboardingDef(): WorkflowDef {
  return {
    id: "wf-onboarding",
    name: "onboarding",
    version: 1,
    source: "template",
    yaml: ONBOARDING.yaml,
    json: ONBOARDING.json as WorkflowDef["json"],
    checksum: "sha256:onboarding",
    is_active: true,
    is_default: true,
    created_by: "template:joiners-leavers",
  };
}

/** The badge task, as the interpreter asks it: the `task` of `t-hand-over-badge`, word for word. */
function badgeTask(): Record<string, unknown> {
  const task = ONBOARDING.badge_task;
  return { summary: task.title, instructions: task.instructions, form: task.form, attest: task.attest };
}

const TOTALS = { tokens_in: 81_000, tokens_out: 6_000, cost_usd: 3.37, cost_eur: 3.1, duration_s: 600, runs: 1 };

/** Paul Martin starts soon: account, groups and laptop are done, the badge is to hand over. */
const waitingForBadge: WorkItemDto = {
  id: "hr1",
  project_slug: SLUG,
  tracker_key: "people-ops#7",
  title: "Paul Martin joins as data analyst",
  state: "laptop_shipped",
  state_display: "Laptop ordered, enrolled, shipped",
  workflow_name: "onboarding",
  workflow_version: 1,
  paused: false,
  fields: {
    full_name: "Paul Martin",
    upn: "paul.martin@acme.test",
    start_date: new Date(now + 3 * 24 * 3_600_000).toISOString().slice(0, 10),
    job: "Data analyst",
    group: "grp-data",
    sensitive_group: "grp-finance-read",
    address: "12 Harbour Street, Lyon",
  },
  totals: TOTALS,
  pending_request: {
    id: "hr1-badge",
    work_item_id: "hr1",
    transition_id: "t-hand-over-badge",
    kind: "task",
    payload: badgeTask(),
    requested_at: iso(50),
    due_at: iso(50 - 8 * 60),
  },
  created_at: iso(60 * 24 * 2),
};

/** The tickets of the project; a request filed from the board joins them for the page's lifetime. */
const items: WorkItemDto[] = [waitingForBadge];

/** What the demo shows of the project, for the English check of the fixtures. */
export const peopleOpsFixtures = { peopleOps, peopleOpsWorkflows, waitingForBadge };

function file(body: WorkItemCreate): WorkItemDto {
  const item: WorkItemDto = {
    id: `hr${items.length + 1}`,
    project_slug: SLUG,
    tracker_key: `people-ops#${7 + items.length}`,
    title: body.title,
    state: "requested",
    state_display: "Request received",
    workflow_name: "onboarding",
    workflow_version: 1,
    paused: false,
    fields: body.fields ?? {},
    totals: {},
    created_at: new Date().toISOString(),
  };
  items.push(item);
  return item;
}

function journey(item: WorkItemDto): WorkItemJourney {
  const { validation } = ONBOARDING;
  const path: Array<[string | null, string, string | null]> =
    item.id === waitingForBadge.id
      ? [
          [null, "requested", null],
          ["requested", "planned", "t-plan"],
          ["planned", "approved", "t-approve"],
          ["approved", "accounts_created", "t-create-accounts"],
          ["accounts_created", "sensitive_granted", "t-grant-sensitive"],
          ["sensitive_granted", "laptop_shipped", "t-ship-laptop"],
        ]
      : [[null, item.state, null]];
  const moves: JourneyMove[] = path.map(([from, to, transition], index) => ({
    at: iso(60 * 24 * 2 - index * 60),
    from,
    to,
    kind: from === null ? "start" : "nominal",
    transition_id: transition,
  }));
  const steps: JourneyStep[] =
    item.id === waitingForBadge.id
      ? [
          {
            id: "r-plan-1",
            kind: "agent",
            transition_id: "t-plan",
            actor: "coordinator",
            role: "coordination",
            attempt: 1,
            status: "succeeded",
            started_at: iso(60 * 24 * 2),
            ended_at: iso(60 * 24 * 2 - 10),
            summary: "Access plan: account, the data group, the finance read group, a 14-inch laptop.",
            cost_usd: 3.37,
          },
          {
            id: "hr-approval-1",
            kind: "human",
            transition_id: "t-approve",
            actor: "hr",
            role: "approval",
            attempt: 1,
            status: "approved",
            started_at: iso(60 * 24 * 2 - 10),
            ended_at: iso(60 * 24 * 2 - 60),
            decided_by: "augustin@varga.dev",
          },
        ]
      : [];
  return {
    work_item_id: item.id,
    tracker_key: item.tracker_key,
    workflow_name: item.workflow_name,
    workflow_version: 1,
    initial: "requested",
    state: item.state,
    closed: false,
    graph: validation.graph!,
    process: validation.process ?? [],
    moves,
    steps,
  };
}

/**
 * The routes of the HR project and of its tickets; `undefined` when the path is not theirs. Its
 * workflows are told apart from the software ones by their YAML, as the API would.
 */
export function peopleOpsApi(method: string, route: string, body: unknown): unknown {
  if (method === "POST" && route === "/workflows/validate") {
    const text = String((body as { yaml?: string } | null)?.yaml ?? "");
    return text === ONBOARDING.yaml ? ONBOARDING.validation : undefined;
  }
  const project = /^\/projects\/(?:[^/]+:)?people-ops(\/.*)?$/.exec(route);
  if (project) {
    const rest = project[1] ?? "";
    if (method === "POST" && rest === "/work-items") return structuredClone(file(body as WorkItemCreate));
    if (method !== "GET") return undefined;
    if (rest === "") return peopleOps;
    // Copies: the tickets change for the page's lifetime, and React Query keeps a reference it was
    // already given as unchanged.
    if (rest === "/work-items") return { items: structuredClone(items), meta: { has_more: false } };
    if (rest === "/workflows") return peopleOpsWorkflows;
    if (rest === "/workflow" || rest === "/workflows/onboarding") return onboardingDef();
    if (rest === "/workflow-routing") return { default: "onboarding", rules: [] };
    if (rest === "/connectors") return [{ id: "c-tracker-hr", kind: "tracker", type: "internal", config: {}, secret_refs: {}, status: "ok" }];
    return undefined;
  }
  const ticket = /^\/work-items\/(hr\d+)(\/[a-z-]+)?$/.exec(route);
  const item = ticket ? items.find((i) => i.id === ticket[1]) : undefined;
  if (!ticket || !item) return undefined;
  if (method === "POST" && ticket[2] === "/decisions") {
    // As the API does: the task's values join the ticket's fields, and the request closes.
    const decision = body as { kind: string; values?: Record<string, unknown> };
    const decided = { ...item.pending_request!, decided_by: "augustin@varga.dev", decided_at: new Date().toISOString(), decision };
    if (decision.kind === "complete") {
      Object.assign(item, { fields: { ...item.fields, ...decision.values }, state: "badge_issued", state_display: "Badge handed over" });
    } else {
      Object.assign(item, { state: "needs_review", state_display: "To review" });
    }
    item.pending_request = null;
    return decided;
  }
  if (method !== "GET") return undefined;
  if (!ticket[2]) return structuredClone(item);
  if (ticket[2] === "/journey") return journey(item);
  if (["/timeline", "/hand-offs", "/runs"].includes(ticket[2])) return [];
  return undefined;
}
