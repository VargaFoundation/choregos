// SPDX-License-Identifier: Apache-2.0
/**
 * The demo's live feed (S67-27). The mock API has no event stream, so the live view plays a short
 * scripted story on the workflow's own lanes: runs start, spend grows, a card moves on, one run
 * fails, one item waits for a person and is decided. It then starts over, so a screen left open
 * keeps moving. The steps are the stream's own messages (`activity.upsert`, `activity.ended`) and
 * work item snapshots: the page reduces them with the same code as it reduces a real stream.
 */
import type { WorkflowGraph, WorkItemDto } from "@/lib/types";
import type { LiveEntry, LiveStep } from "@/lib/live-flow";

export interface ScriptedStep {
  /** Milliseconds after the start of the script. */
  after: number;
  step: LiveStep;
}

export interface LiveScript {
  items: WorkItemDto[];
  steps: ScriptedStep[];
  /** When the story starts over, after its last step. */
  restartAfter: number;
}

const TITLES: [string, string][] = [
  ["ACME-101", "Fix the login redirect"],
  ["ACME-102", "Add rate limiting to the export API"],
  ["ACME-103", "Rewrite the invoice footer"],
  ["ACME-104", "Move the audit log to partitions"],
  ["ACME-105", "Upgrade the e-mail templates"],
  ["ACME-106", "Retire the legacy webhook"],
];

/** The lanes the story walks: the first, the second, a waiting one if the graph has it, the last. */
function lanesOf(graph: WorkflowGraph | undefined): {
  first: string;
  second: string;
  wait: string;
  last: string;
} {
  const nodes = graph?.nodes ?? [];
  const open = nodes.filter((node) => !node.terminal);
  const ids = nodes.map((node) => node.id);
  const first = ids[0] ?? "todo";
  const second = open[1]?.id ?? first;
  const wait = nodes.find((node) => node.kind === "wait")?.id ?? second;
  const last =
    nodes.find((node) => node.terminal)?.id ?? ids[ids.length - 1] ?? first;
  return { first, second, wait, last };
}

export function liveScript(
  graph: WorkflowGraph | undefined,
  workflow: string,
  now: number,
): LiveScript {
  const lane = lanesOf(graph);
  const at = (ms: number) => new Date(now + ms).toISOString();
  const start = (n: number): WorkItemDto =>
    ({
      id: `demo-${n}`,
      project_slug: "demo",
      tracker_key: TITLES[n]?.[0] ?? `ACME-${n}`,
      title: TITLES[n]?.[1] ?? `Item ${n}`,
      state: lane.first,
      workflow_name: workflow,
      totals: {},
    }) as unknown as WorkItemDto;
  const world: WorkItemDto[] = TITLES.map((_, n) => start(n));
  const put = (n: number, patch: Record<string, unknown>) => {
    world[n] = { ...world[n], ...patch } as WorkItemDto;
  };
  put(2, { state: lane.second });
  put(3, { state: lane.second });
  put(4, { state: lane.last, closed_at: at(-3_600_000) });
  const initial = world.map((item) => ({ ...item }));

  const run = (
    n: number,
    id: string,
    stage: string,
    agent: string,
    ms: number,
    cost?: number,
  ): LiveStep => ({
    event: "activity.upsert",
    data: {
      kind: "run",
      id,
      status: "running",
      project_slug: "demo",
      work_item_id: `demo-${n}`,
      workflow,
      stage_role: stage,
      agent_slug: agent,
      started_at: at(ms),
      can_stop: true,
      ...(cost === undefined
        ? {}
        : {
            spend: {
              tokens_in: 1,
              tokens_out: 1,
              tokens_cached: 0,
              cost_usd: cost,
              read_at: at(ms),
            },
          }),
    } as LiveEntry,
  });
  const ended = (
    n: number,
    id: string,
    status: string,
    stage: string,
    startedMs: number,
    endedMs: number,
    failure?: LiveEntry["failure"],
  ): LiveStep => ({
    event: "activity.ended",
    data: {
      kind: "run",
      id,
      status,
      project_slug: "demo",
      work_item_id: `demo-${n}`,
      workflow,
      stage_role: stage,
      started_at: at(startedMs),
      ended_at: at(endedMs),
      can_stop: false,
      failure,
    } as unknown as LiveEntry,
  });
  const items = (): LiveStep => ({ items: world.map((item) => ({ ...item })) });

  const steps: ScriptedStep[] = [];
  const add = (after: number, step: LiveStep) => steps.push({ after, step });

  add(1_200, run(0, "demo-r1", "triage", "triager", 1_200));
  add(2_600, run(2, "demo-r2", "developer", "developer", 2_600, 0.12));
  add(4_200, run(0, "demo-r1", "triage", "triager", 1_200, 0.04));
  add(6_000, ended(0, "demo-r1", "succeeded", "triage", 1_200, 6_000));
  put(0, { state: lane.second });
  add(6_100, items());
  add(7_600, run(0, "demo-r3", "developer", "developer", 7_600, 0.02));
  add(9_000, run(2, "demo-r2", "developer", "developer", 2_600, 0.46));
  add(
    10_400,
    ended(2, "demo-r2", "timed_out", "developer", 2_600, 10_400, {
      reason: "timeout",
      summary:
        "The run hit its 8 minute limit while the test suite was still running.",
    }),
  );
  put(1, {
    pending_request: {
      id: "demo-req",
      kind: "approval",
      payload: { title: "Approve the rate limit plan" },
      decided_at: null,
    },
  });
  add(12_000, items());
  add(14_000, ended(0, "demo-r3", "succeeded", "developer", 7_600, 14_000));
  put(0, { state: lane.wait });
  add(14_100, items());
  put(1, { pending_request: null, state: lane.second });
  add(16_500, items());
  add(18_000, run(3, "demo-r4", "reviewer", "reviewer", 18_000, 0.2));
  add(21_000, ended(3, "demo-r4", "succeeded", "reviewer", 18_000, 21_000));
  put(3, { state: lane.last, closed_at: at(21_100) });
  add(21_100, items());
  return { items: initial, steps, restartAfter: 25_000 };
}

/**
 * The workflow the demo's flow draws (S67-29): a realistic delivery line, with a human approval, a
 * retry loop (the review sends work back to the implementation) and an escalation. The mock API's
 * `default-simple` has four states, too few to show anything travelling.
 */
export const demoFlowGraph = {
  nodes: [
    { id: "triage", display: "Triage", kind: "normal", lane: "agent" },
    { id: "refine", display: "Refine", kind: "normal", lane: "agent" },
    { id: "plan", display: "Plan", kind: "normal", lane: "agent" },
    { id: "approval", display: "Plan approval", kind: "wait", lane: "human" },
    { id: "implement", display: "Implement", kind: "normal", lane: "agent" },
    { id: "review", display: "Review", kind: "normal", lane: "agent" },
    { id: "done", display: "Done", kind: "normal", terminal: true, lane: "terminal" },
    { id: "needs_human", display: "Needs a human", kind: "wait", lane: "human" },
  ],
  edges: [
    { id: "t-triage", from: "triage", to: "refine", kind: "nominal", actor: "triager" },
    { id: "t-refine", from: "refine", to: "plan", kind: "nominal", actor: "refiner" },
    { id: "t-plan", from: "plan", to: "approval", kind: "nominal", actor: "planner" },
    { id: "t-approve", from: "approval", to: "implement", kind: "nominal", actor: "a maintainer" },
    { id: "t-implement", from: "implement", to: "review", kind: "nominal", actor: "developer" },
    { id: "t-review", from: "review", to: "done", kind: "nominal", actor: "reviewer" },
    { id: "implement->needs_human:escalate:when retries run out", from: "implement", to: "needs_human", kind: "escalate", label: "when retries run out" },
    { id: "review->implement:reject:if rejected", from: "review", to: "implement", kind: "reject", label: "if rejected" },
  ],
};

/** The demo's flow story on `demoFlowGraph`: tickets walk the line, one run fails and retries, one is sent back. */
export function flowScript(workflow: string, now: number): LiveScript {
  const at = (ms: number) => new Date(now + ms).toISOString();
  const titles: [string, string][] = [
    ["ACME-201", "Fix the login redirect"],
    ["ACME-202", "Add rate limiting to the export API"],
    ["ACME-203", "Rewrite the invoice footer"],
    ["ACME-204", "Move the audit log to partitions"],
    ["ACME-205", "Upgrade the e-mail templates"],
    ["ACME-206", "Retire the legacy webhook"],
    ["ACME-207", "Cache the price list"],
  ];
  const world: WorkItemDto[] = titles.map(
    ([key, title], n) =>
      ({
        id: `demo-${n}`,
        project_slug: "demo",
        tracker_key: key,
        title,
        state: "triage",
        workflow_name: workflow,
        totals: {},
      }) as unknown as WorkItemDto,
  );
  const put = (n: number, patch: Record<string, unknown>) => {
    world[n] = { ...world[n], ...patch } as WorkItemDto;
  };
  const request = {
    id: "demo-req",
    kind: "approval",
    payload: { title: "Approve the plan" },
    decided_at: null,
  };
  put(0, { state: "triage" });
  put(1, { state: "triage" });
  put(2, { state: "implement" });
  put(3, { state: "review" });
  put(4, { state: "done", closed_at: at(-3_600_000) });
  put(5, { state: "approval", pending_request: request });
  put(6, { state: "plan" });
  const initial = world.map((item) => ({ ...item }));

  const run = (n: number, id: string, stage: string, agent: string, ms: number, cost: number): LiveStep => ({
    event: "activity.upsert",
    data: {
      kind: "run",
      id,
      status: "running",
      project_slug: "demo",
      work_item_id: `demo-${n}`,
      workflow,
      stage_role: stage,
      agent_slug: agent,
      started_at: at(ms),
      can_stop: true,
      spend: { tokens_in: 1, tokens_out: 1, tokens_cached: 0, cost_usd: cost, read_at: at(ms) },
    } as LiveEntry,
  });
  const ended = (
    n: number,
    id: string,
    status: string,
    stage: string,
    from: number,
    to: number,
    failure?: LiveEntry["failure"],
  ): LiveStep => ({
    event: "activity.ended",
    data: {
      kind: "run",
      id,
      status,
      project_slug: "demo",
      work_item_id: `demo-${n}`,
      workflow,
      stage_role: stage,
      started_at: at(from),
      ended_at: at(to),
      can_stop: false,
      failure,
    } as unknown as LiveEntry,
  });
  const snapshot = (): LiveStep => ({ items: world.map((item) => ({ ...item })) });

  const steps: ScriptedStep[] = [];
  const add = (after: number, step: LiveStep) => steps.push({ after, step });
  const move = (after: number, n: number, state: string, patch: Record<string, unknown> = {}) => {
    put(n, { state, ...patch });
    add(after, snapshot());
  };

  // Work starts in four places at once.
  add(1_000, run(0, "f-a1", "triage", "triager", 1_000, 0.01));
  add(1_400, run(2, "f-b1", "developer", "developer", 1_400, 0.18));
  add(1_800, run(3, "f-c1", "reviewer", "reviewer", 1_800, 0.09));
  add(2_200, run(6, "f-d1", "planner", "planner", 2_200, 0.05));
  // The review passes: the ticket walks to Done.
  add(5_200, ended(3, "f-c1", "succeeded", "review", 1_800, 5_200));
  move(5_300, 3, "done", { closed_at: at(5_300) });
  // The triage passes; the refinement starts at once.
  add(6_400, ended(0, "f-a1", "succeeded", "triage", 1_000, 6_400));
  move(6_500, 0, "refine");
  add(7_000, run(0, "f-a2", "refiner", "refiner", 7_000, 0.02));
  // The plan is ready and waits for a person.
  add(7_800, ended(6, "f-d1", "succeeded", "plan", 2_200, 7_800));
  move(7_900, 6, "approval", { pending_request: request });
  // The implementation of 203 times out: the run fails and is retried.
  add(
    9_600,
    ended(2, "f-b1", "timed_out", "implement", 1_400, 9_600, {
      reason: "timeout",
      summary: "The run hit its 8 minute limit while the test suite was still running.",
    }),
  );
  add(10_000, run(1, "f-e1", "triager", "triager", 10_000, 0.01));
  // A maintainer approves the plan of 206: it goes on to implementation.
  move(11_600, 5, "implement", { pending_request: null });
  add(11_900, run(5, "f-f1", "developer", "developer", 11_900, 0.04));
  add(12_400, run(2, "f-b2", "developer", "developer", 12_400, 0.02));
  add(13_500, ended(0, "f-a2", "succeeded", "refine", 7_000, 13_500));
  move(13_600, 0, "plan");
  add(14_200, run(0, "f-a3", "planner", "planner", 14_200, 0.02));
  add(15_000, ended(1, "f-e1", "succeeded", "triage", 10_000, 15_000));
  move(15_100, 1, "refine");
  // The retry succeeds: 203 goes to the review, which sends it back (the retry loop).
  add(16_200, ended(2, "f-b2", "succeeded", "implement", 12_400, 16_200));
  move(16_300, 2, "review");
  add(16_800, run(2, "f-b3", "reviewer", "reviewer", 16_800, 0.03));
  add(19_800, ended(2, "f-b3", "succeeded", "review", 16_800, 19_800));
  move(19_900, 2, "implement");
  add(20_400, run(2, "f-b4", "developer", "developer", 20_400, 0.02));
  add(20_800, ended(0, "f-a3", "succeeded", "plan", 14_200, 20_800));
  move(20_900, 0, "approval", { pending_request: { ...request, id: "demo-req-2" } });
  add(23_000, ended(5, "f-f1", "succeeded", "implement", 11_900, 23_000));
  move(23_100, 5, "review");
  add(23_600, run(5, "f-f2", "reviewer", "reviewer", 23_600, 0.03));
  add(26_400, ended(2, "f-b4", "succeeded", "implement", 20_400, 26_400));
  move(26_500, 2, "review");
  add(27_400, ended(5, "f-f2", "succeeded", "review", 23_600, 27_400));
  move(27_500, 5, "done", { closed_at: at(27_500) });
  add(27_900, run(2, "f-b5", "reviewer", "reviewer", 27_900, 0.03));
  add(31_000, ended(2, "f-b5", "succeeded", "review", 27_900, 31_000));
  move(31_100, 2, "done", { closed_at: at(31_100) });
  return { items: initial, steps, restartAfter: 36_000 };
}
