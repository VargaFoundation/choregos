// SPDX-License-Identifier: Apache-2.0
import type { ProcessStep } from "@/lib/types";

/** What decides how long a transition waits, as the workflow writes it. */
export type TimingInput = {
  actorType?: string | null;
  /** The SLA of a human actor (`sla_hours`). */
  slaHours?: number | null;
  /** The transition's own limit (`timeout_hours`). */
  timeoutHours?: number | null;
  /** Where a rejection sends the item (`on_reject`), by its display name. */
  onReject?: string | null;
  /** The workflow's `defaults.on_timeout`, by its display name. */
  onTimeout?: string | null;
  /** The state the transition leaves, by its display name. */
  from?: string | null;
  /** The guarantees of the transition that wait for an outside event (CI, review, scans). */
  asyncGates?: string[];
  /** A release train step. */
  train?: boolean;
};

/** The SLA a human step gets when its actor sets none, and the timeout's multiple of it (the interpreter's). */
const DEFAULT_SLA_HOURS = 24;
const TIMEOUT_TIMES_SLA = 3;
const DEFAULT_GATE_WAIT_HOURS = 24;
const DEFAULT_TRAIN_WAIT_HOURS = 72;

/**
 * How long a step waits and what happens then, in one sentence, from the interpreter's own rules: a
 * person is reminded at the SLA and, with no decision by the timeout (`timeout_hours`, else three times
 * the SLA), the item is treated as rejected — `on_reject` when the transition has one, else the
 * workflow's `on_timeout`, else it goes back to where it was. A guarantee that waits for the CI or a
 * review waits up to `timeout_hours` (24 h by default); a release train, 72 h. Null when nothing waits.
 */
export function timingSentence(input: TimingInput): string | null {
  if (input.actorType === "human") {
    const sla = input.slaHours ?? DEFAULT_SLA_HOURS;
    const timeout = input.timeoutHours ?? sla * TIMEOUT_TIMES_SLA;
    const where = input.onReject
      ? `goes back to “${input.onReject}” as if rejected`
      : input.onTimeout
        ? `goes to “${input.onTimeout}”`
        : `stays in “${input.from ?? "its state"}” for a person to pick up`;
    return (
      `The group is reminded after ${sla} h${input.slaHours ? "" : " (no SLA set: 24 h)"}. ` +
      `With no decision after ${timeout} h${input.timeoutHours ? "" : ` (no limit set: ${TIMEOUT_TIMES_SLA} × the SLA)`}, ` +
      `the item ${where}.`
    );
  }
  if (input.train) {
    return `It waits for the release train up to ${input.timeoutHours ?? DEFAULT_TRAIN_WAIT_HOURS} h${input.timeoutHours ? "" : " (no limit set)"}.`;
  }
  if (input.asyncGates?.length) {
    const names = input.asyncGates.map((g) => g.replace(/_/g, " ")).join(", ");
    return `It waits for ${names} up to ${input.timeoutHours ?? DEFAULT_GATE_WAIT_HOURS} h${input.timeoutHours ? "" : " (no limit set)"}.`;
  }
  if (input.timeoutHours) return `Nothing on this step waits for an outside event: the ${input.timeoutHours} h limit applies to nothing yet.`;
  return null;
}

/**
 * What "Default" means next to a workflow: the accent badge read as an error on the map (captures of
 * 2026-10-28); it is a neutral fact, said in its tooltip.
 */
export const DEFAULT_WORKFLOW_HINT =
  "The project's default: a work item runs it unless it names another workflow or a routing rule sends it elsewhere.";

/** The edges and nodes of a workflow's graph, as far as the timing sentence reads them. */
type GraphForTiming = { nodes: unknown[]; edges: unknown[] } | null | undefined;

/**
 * The timing sentence of one step of a workflow (the map's and the process view's editors): the step's
 * actor and guarantees, the transition's `timeout_hours` as the draft has it, where a rejection from its
 * state goes, and the workflow's `defaults.on_timeout`.
 */
export function timingOfStep(
  step: ProcessStep,
  { timeoutHours, graph, onTimeout }: { timeoutHours?: number | null; graph: GraphForTiming; onTimeout?: string | null },
): string | null {
  const nodes = (graph?.nodes ?? []) as { id: string; display?: string }[];
  const edges = (graph?.edges ?? []) as { kind?: string; from: string; to: string }[];
  const displayOf = (id?: string | null) => (id ? (nodes.find((n) => n.id === id)?.display ?? id) : null);
  const rejectTarget = edges.find((e) => e.kind === "reject" && e.from === step.from)?.to;
  return timingSentence({
    actorType: step.actor_type,
    slaHours: step.sla_hours,
    timeoutHours,
    onReject: displayOf(rejectTarget),
    onTimeout: displayOf(onTimeout),
    from: step.from_display ?? step.from,
    asyncGates: (step.gates ?? []).filter((g) => g.asynchronous).map((g) => g.name ?? ""),
    train: step.actor_type === "release_train",
  });
}
