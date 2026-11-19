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
