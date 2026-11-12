// SPDX-License-Identifier: Apache-2.0
/**
 * The demo's Activity (S64-06, ADR 0142): two runs and a session in flight, two runs that ended in the
 * last hour. One running run is the reader's to stop, the other is not (`can_stop: false`); the session
 * has no spend the gateway could read (`spend: null`, shown "—"). Stopping a run moves it to the
 * finished entries, cancelled; stopping it again answers 409, as the API does. Stopping an agent
 * cancels its runs in flight and answers the counts. A conversation turn of the `concierge` assistant
 * is in flight too, for its owner: stopping the assistant cancels it and answers how many turns were
 * cancelled; resuming answers 409 when the assistant is not stopped (S64-09, ADR 0142 §4 bis).
 */
import { ApiError } from "@/lib/api";
import type { Assistant, AssistantStopResult } from "@/lib/assistants-api";
import type { ActivityEntry, ActivityList, AgentStopResult, Run } from "@/lib/types";

/** The demo's assistant installations, by id: a report grouped by assistant names each by its slug. */
export const MOCK_INSTALLATIONS: Record<string, string> = { "inst-concierge": "concierge", "inst-analyst": "analyst" };

const iso = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const spend = (tokensIn: number, tokensOut: number, cost: number, booked = false) => ({
  tokens_in: tokensIn,
  tokens_out: tokensOut,
  tokens_cached: Math.round(tokensIn * 0.4),
  cost_usd: cost,
  read_at: iso(0),
  booked,
});

export function seedActivity(): ActivityList {
  return {
    in_flight: [
      {
        kind: "run",
        id: "r3",
        status: "running",
        project_slug: "billing-api",
        work_item_id: "w1",
        work_item_key: "BILL-42",
        workflow: "delivery",
        stage_role: "implement",
        agent_slug: "coordinator",
        model: "claude-sonnet",
        initiated_by: "lea@acme.dev",
        started_at: iso(12),
        last_event: "tool_call",
        steps: 14,
        spend: spend(184_000, 12_400, 1.92),
        can_stop: true,
      },
      {
        kind: "run",
        id: "r-checkout-review",
        status: "queued",
        project_slug: "checkout-web",
        work_item_id: "w9",
        work_item_key: "SHOP-7",
        workflow: "delivery",
        stage_role: "review",
        agent_slug: "reviewer",
        model: "claude-haiku",
        initiated_by: "max@acme.dev",
        started_at: iso(2),
        steps: 0,
        spend: spend(0, 0, 0),
        can_stop: false,
      },
      {
        kind: "session",
        id: "s-7f3a91c2",
        status: "open",
        agent_slug: "support-bot",
        model: "claude-sonnet",
        initiated_by: "lea@acme.dev",
        started_at: iso(25),
        steps: 6,
        spend: null,
        can_stop: false,
      },
      {
        // A turn has no stop of its own: its assistant's stop, or its agent's, is the stop.
        kind: "turn",
        id: "t-0c41d9e7",
        status: "running",
        agent_slug: "concierge-agent",
        assistant: "inst-concierge",
        model: "claude-haiku",
        initiated_by: "nina@acme.dev",
        started_at: iso(1),
        last_event: "tool_call",
        steps: 3,
        spend: spend(8_200, 640, 0.02),
        can_stop: false,
      },
    ],
    finished: [
      {
        kind: "run",
        id: "r2",
        status: "succeeded",
        project_slug: "billing-api",
        work_item_id: "w1",
        work_item_key: "BILL-42",
        workflow: "delivery",
        stage_role: "plan",
        agent_slug: "coordinator",
        model: "claude-sonnet",
        started_at: iso(40),
        ended_at: iso(31),
        steps: 22,
        spend: spend(96_000, 7_000, 0.88, true),
        can_stop: false,
      },
      {
        kind: "run",
        id: "r1",
        status: "failed",
        project_slug: "billing-api",
        work_item_id: "w2",
        work_item_key: "BILL-14",
        workflow: "delivery",
        stage_role: "review",
        agent_slug: "reviewer",
        model: "claude-haiku",
        started_at: iso(55),
        ended_at: iso(50),
        steps: 9,
        spend: spend(31_000, 2_200, 0.12, true),
        can_stop: false,
      },
    ],
  };
}

let state = seedActivity();
/** Who stopped which assistant (by slug), when and why. */
let stoppedAssistants = new Map<string, NonNullable<Assistant["stopped"]>>();

/** Back to the seed: a test starts from the same list. */
export function resetActivity(): void {
  state = seedActivity();
  stoppedAssistants = new Map();
}

const installationOf = (slug: string) => Object.entries(MOCK_INSTALLATIONS).find(([, s]) => s === slug)?.[0];

function narrowed(list: ActivityEntry[], query: URLSearchParams): ActivityEntry[] {
  const project = query.get("project");
  const workflow = query.get("workflow");
  const agent = query.get("agent");
  const assistant = query.get("assistant");
  const kind = query.get("kind");
  return list.filter(
    (e) =>
      (!project || e.project_slug === project) &&
      (!workflow || e.workflow === workflow) &&
      (!agent || e.agent_slug === agent) &&
      (!assistant || e.assistant === assistant) &&
      (!kind || e.kind === kind),
  );
}

function cancel(entry: ActivityEntry, reason: string): void {
  state.in_flight = state.in_flight.filter((e) => e !== entry);
  state.finished.unshift({ ...entry, status: "cancelled", ended_at: iso(0), last_event: `${entry.kind}.cancelled: ${reason}`, can_stop: false });
}

function reasonOf(body: unknown): string {
  const reason = typeof (body as { reason?: unknown })?.reason === "string" ? ((body as { reason: string }).reason ?? "").trim() : "";
  if (!reason || reason.length > 500) {
    throw new ApiError(422, { title: "Unprocessable Entity", detail: "a stop needs a reason of 1 to 500 characters" });
  }
  return reason;
}

/** The Activity routes of the demo; `undefined` for any other path. */
export function activityApi(method: string, path: string, body: unknown): unknown {
  const [route = "", search = ""] = path.split("?");
  if (method === "GET" && /^\/orgs\/[^/]+\/activity$/.test(route)) {
    const query = new URLSearchParams(search);
    return structuredClone({ in_flight: narrowed(state.in_flight, query), finished: narrowed(state.finished, query) });
  }
  const cancelled = /^\/runs\/([^/]+)\/cancel$/.exec(route);
  if (method === "POST" && cancelled) {
    const id = decodeURIComponent(cancelled[1]!);
    const reason = reasonOf(body);
    const entry = state.in_flight.find((e) => e.kind === "run" && e.id === id);
    if (!entry) {
      const ended = state.finished.find((e) => e.id === id);
      throw new ApiError(409, { title: "Conflict", detail: `the run has already ended (${ended?.status ?? "unknown"})` });
    }
    if (!entry.can_stop) {
      throw new ApiError(403, { title: "Forbidden", detail: "stopping a run needs item:control on its project" });
    }
    cancel(entry, reason);
    return { id, status: "cancelled", stage_role: entry.stage_role ?? "", attempt: 1 } as unknown as Run;
  }
  const stopped = /^\/orgs\/[^/]+\/agents\/([^/]+)\/stop$/.exec(route);
  if (method === "POST" && stopped) {
    const slug = decodeURIComponent(stopped[1]!);
    const reason = reasonOf(body);
    const mine = state.in_flight.filter((e) => e.agent_slug === slug);
    for (const entry of mine.filter((e) => e.kind === "run")) cancel(entry, reason);
    const sessions = mine.filter((e) => e.kind === "session");
    state.in_flight = state.in_flight.filter((e) => !sessions.includes(e));
    const result: AgentStopResult = {
      slug,
      status: "suspended",
      runs_cancelled: mine.filter((e) => e.kind === "run").length,
      sessions_closed: sessions.length,
      turns_cancelled: mine.filter((e) => e.kind === "turn").length,
      not_stopped: [],
    };
    return result;
  }
  const assistant = /^\/orgs\/[^/]+\/assistants\/([^/]+)\/(stop|resume)$/.exec(route);
  if (method === "POST" && assistant) {
    const slug = decodeURIComponent(assistant[1]!);
    const id = installationOf(slug);
    if (!id) throw new ApiError(404, { title: "Not Found", detail: `no assistant ${slug}` });
    if (assistant[2] === "resume") {
      if (!stoppedAssistants.delete(slug)) throw new ApiError(409, { title: "Conflict", detail: "the assistant is not stopped" });
      return { slug, status: "ready", stopped: null } as unknown as Assistant;
    }
    const reason = reasonOf(body);
    stoppedAssistants.set(slug, { at: iso(0), by: "lea@acme.dev", reason });
    const turns = state.in_flight.filter((e) => e.kind === "turn" && e.assistant === id);
    for (const entry of turns) cancel(entry, reason);
    const result: AssistantStopResult = { slug, status: "suspended", turns_cancelled: turns.length, not_stopped: [] };
    return result;
  }
  return undefined;
}
