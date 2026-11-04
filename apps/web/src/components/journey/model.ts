// SPDX-License-Identifier: Apache-2.0
/**
 * A work item's journey as a model (S22-02): what the animated map draws, without React.
 *
 * A workflow's map (S21-07) says its SHAPE. Here we follow ONE work item: every box is a
 * transition carried by an actor (an agent, a person, the platform, the train), and its state at
 * a given instant is deduced from what the API returned (`/work-items/{id}/journey`): the moves
 * attached to their edge, and each actor's attempts. The same computation serves the live view
 * (the instant is "now") and the replay (the instant moves from one event to the next).
 *
 * Pure, deterministic functions: two reads of the same journey give the same map.
 */
import type { JourneyMove, JourneyStep, ProcessStep, WorkItemJourney } from "@/lib/types";
import {
  type GraphEdge,
  nominalPath,
  isNominal,
  type Graph,
  secondaryLabel,
  type GraphNode,
} from "../workflows/map/layout";

/** Who carries a step: the shape of its box, and what it says in so many words. */
export type StepKind = "agent" | "human" | "platform" | "train" | "decision";

export type Step = {
  /** The transition's identifier. */
  id: string;
  from: string;
  to: string;
  kind: StepKind;
  actor: string | null;
  /** The name written on the box: "Developer", "Maintainers", "Release train". */
  label: string;
  /** What the actor does, in a few words: "writes the change". */
  action: string;
  gates: string[];
  /** The number of rounds allowed (`on_fail.max_attempts`), when the graph says it. */
  maxRounds: number | null;
  /** On the nominal path (`true`) or on a detour (a review rework, a CI repair). */
  onPath: boolean;
  /** The process view's sentence: what the transition does, under which conditions. */
  sentence: string | null;
  /** The registry agent the actor names (`slug` or `slug@N`), when it names one (S39-13). */
  agentRef: string | null;
  /** The documents the step produces and reads, as the workflow declares them. */
  outputs: string[];
  inputs: string[];
  /** The step changes the work item's branch (a gate reads the diff), though it lists no output. */
  writesBranch: boolean;
};

/** A state off the path that one comes back from: its transition's box, where one falls in from, where one returns to. */
export type Detour = {
  state: string;
  step: Step;
  sources: string[];
  back: string;
};

/** A drawn secondary edge: a loop back on the path, or the entry and the exit of a detour. */
export type Arc = {
  key: string;
  /** The state the move leaves and the one it leads to: what the journey's moves name. */
  from: string;
  to: string;
  /** The box the arc leaves, and the one it reaches (a step, or an exception state). */
  fromStep: string;
  target: { step: string } | { state: string } | { exception: string };
  kind: "loop" | "detour" | "back" | "exception";
  label: string;
};

export type Phase = { key: string; label: string; steps: string[] };

export type JourneyModel = {
  initial: string;
  path: string[];
  /** The path's steps, in order. */
  steps: Step[];
  detours: Detour[];
  arcs: Arc[];
  /** The states one only falls into through an escalation or a default, and the ends off the path. */
  exceptions: { id: string; display: string; terminal: boolean }[];
  phases: Phase[];
  name: (state: string) => string;
  /** Every step, path and detours, by identifier. */
  byId: Map<string, Step>;
};

const ACTIONS_BY_ROLE: Record<string, string> = {
  triage: "sizes the work item",
  refine: "writes the specification",
  specify: "writes the specification",
  plan: "plans the work",
  implement: "writes the change",
  verify: "runs the checks",
  test: "runs the checks",
  review: "reviews the change",
  address_review: "addresses the review",
  release_notes: "writes the release notes",
  fix_ci: "fixes the CI",
  verify_prod: "checks production",
  research: "gathers the options",
  architect: "writes the decision",
  document: "writes the document",
};

/** "spec_writer" → "Spec writer"; "release-captains" → "Release captains". */
export function humanise(name: string): string {
  const words = name.replace(/[_-]+/g, " ").trim();
  return words ? words[0]!.toUpperCase() + words.slice(1) : name;
}

/** A state's lane says who leaves it (`dsl/graph.py`): what one reads when the edge does not name its actor. */
const KIND_BY_LANE: Record<string, StepKind> = { agent: "agent", human: "human", train: "train", system: "platform", decision: "decision" };

function kindOf(edge: GraphEdge, lane?: string): StepKind {
  if (edge.via === "release_train" || edge.actor_type === "train") return "train";
  if (edge.actor_type === "agent") return "agent";
  if (edge.actor_type === "human") return "human";
  if (edge.actor_type === "decision") return "decision";
  if (edge.actor_type) return "platform";
  return KIND_BY_LANE[lane ?? ""] ?? "platform";
}

/** `on failure (≤3)` → 3; a request for changes does not state its bound in the graph. */
function boundOf(edges: GraphEdge[], from: string): number | null {
  for (const a of edges) {
    if (a.from !== from || a.kind !== "retry") continue;
    const bound = /\(\s*≤\s*(\d+)\s*\)/.exec(a.label ?? "")?.[1];
    if (bound) return Number(bound);
  }
  return null;
}

function actionOf(edge: GraphEdge, kind: StepKind, step: ProcessStep | undefined, name: (e: string) => string): string {
  const text = `${step?.who ?? ""} ${step?.sentence ?? ""}`;
  if (kind === "agent") return ACTIONS_BY_ROLE[edge.role ?? ""] ?? (edge.role ? humanise(edge.role).toLowerCase() : "works");
  if (kind === "train") {
    const env = /release train to ([\w-]+)/.exec(text)?.[1];
    return env ? `deploys to ${env}` : "deploys";
  }
  if (kind === "human") return `approves → ${name(edge.to)}`;
  // A decision model answers its questions (ADR 0121): what it decides, from its role.
  if (kind === "decision") return edge.role ? `decides: ${humanise(edge.role).toLowerCase()}` : "decides";
  if (/opens the pull request/.test(text)) return "opens the pull request";
  if (/merges the pull request/.test(text)) return "merges the pull request";
  if (edge.gates?.length) return `checks ${edge.gates.map((g) => g.replace(/_/g, " ")).join(", ")}`;
  return `moves on → ${name(edge.to)}`;
}

function stepOf(
  edge: GraphEdge,
  edges: GraphEdge[],
  process: Map<string, ProcessStep>,
  name: (e: string) => string,
  onPath: boolean,
  lane?: string,
): Step {
  const kind = kindOf(edge, lane);
  const step = process.get(edge.id ?? "");
  const label =
    kind === "train" ? "Release train" : kind === "platform" ? "Platform" : humanise(edge.actor ?? edge.role ?? "actor");
  return {
    id: edge.id ?? `${edge.from}->${edge.to}`,
    from: edge.from,
    to: edge.to,
    kind,
    actor: edge.actor ?? null,
    label,
    action: actionOf(edge, kind, step, name),
    gates: edge.gates ?? [],
    maxRounds: boundOf(edges, edge.from),
    onPath,
    sentence: step?.sentence ?? null,
    agentRef: step?.agent ?? null,
    outputs: step?.outputs ?? [],
    inputs: step?.inputs ?? [],
    writesBranch: step?.writes_branch ?? false,
  };
}

/** What a box says it produces, on one line: `→ spec_markdown, allowed_paths`, or `→ branch`. */
export function outputsOf(step: Pick<Step, "outputs" | "writesBranch">): string | null {
  const names = [...step.outputs, ...(step.writesBranch ? ["branch"] : [])];
  return names.length > 0 ? `→ ${names.join(", ")}` : null;
}

const SECONDARY = new Set(["reject", "retry", "escalate"]);

export function buildModel(journey: Pick<WorkItemJourney, "graph" | "process" | "initial">): JourneyModel {
  const graph = journey.graph as Graph;
  const nodes = graph.nodes as GraphNode[];
  const edges = (graph.edges as GraphEdge[]).filter((a) => !a.wildcard);
  const byState = new Map(nodes.map((n) => [n.id, n]));
  const name = (state: string) => byState.get(state)?.display ?? state;
  const process = new Map((journey.process ?? []).map((p) => [p.id, p]));
  const path = nominalPath(graph, journey.initial);
  const onPath = new Set(path);

  const steps: Step[] = [];
  for (let i = 0; i + 1 < path.length; i += 1) {
    const edge = edges.find((a) => isNominal(a) && a.from === path[i] && a.to === path[i + 1]);
    if (edge) steps.push(stepOf(edge, edges, process, name, true, byState.get(edge.from)?.lane));
  }
  const byId = new Map(steps.map((e) => [e.id, e]));
  const stepLeaving = (state: string) => steps.find((e) => e.from === state);

  // The detours: a state off the path whose nominal transition leads back ONTO the path.
  const detours: Detour[] = [];
  for (const node of nodes) {
    if (onPath.has(node.id)) continue;
    const exit = edges.find((a) => isNominal(a) && a.from === node.id && onPath.has(a.to));
    if (!exit) continue;
    const step = stepOf(exit, edges, process, name, false, node.lane);
    const sources = steps
      .filter((e) => edges.some((a) => a.from === e.from && a.to === node.id && SECONDARY.has(a.kind ?? "")))
      .map((e) => e.id);
    if (sources.length === 0) continue;
    detours.push({ state: node.id, step, sources, back: exit.to });
    byId.set(step.id, step);
  }
  const inDetour = new Set(detours.map((d) => d.state));

  // The arcs: a loop back on the path (not in place: a retry in the same state is one more
  // round, not an arc), the entry of a detour, its return.
  const arcs: Arc[] = [];
  const seen = new Set<string>();
  const addArc = (arc: Arc) => {
    if (seen.has(arc.key)) return;
    seen.add(arc.key);
    arcs.push(arc);
  };
  for (const e of steps) {
    for (const a of edges) {
      if (a.from !== e.from || a.to === e.from || !SECONDARY.has(a.kind ?? "")) continue;
      if (a.kind === "escalate") continue;
      const label = secondaryLabel(a);
      if (onPath.has(a.to)) {
        const target = stepLeaving(a.to);
        if (!target || path.indexOf(a.to) >= path.indexOf(e.from)) continue;
        addArc({
          key: `${a.from}->${a.to}`,
          from: a.from,
          to: a.to,
          fromStep: e.id,
          target: { state: a.to },
          kind: "loop",
          label,
        });
      } else if (inDetour.has(a.to)) {
        const detour = detours.find((d) => d.state === a.to)!;
        addArc({
          key: `${a.from}->${a.to}`,
          from: a.from,
          to: a.to,
          fromStep: e.id,
          target: { step: detour.step.id },
          kind: "detour",
          label,
        });
      }
    }
  }
  for (const d of detours) {
    addArc({
      key: `${d.state}->${d.back}`,
      from: d.state,
      to: d.back,
      fromStep: d.step.id,
      target: { state: d.back },
      kind: "back",
      label: `Back to ${name(d.back)}`,
    });
  }

  const exceptions = nodes
    .filter((n) => !onPath.has(n.id) && !inDetour.has(n.id))
    .map((n) => ({
      id: n.id,
      display: n.display,
      terminal: Boolean(n.terminal),
    }));

  return {
    initial: path[0] ?? journey.initial ?? "",
    path,
    steps,
    detours,
    arcs,
    exceptions,
    phases: phasesOf(steps, byState),
    name,
    byId,
  };
}

/**
 * The journey's phases, to jump from one step to the next: the consecutive steps that lead to the
 * same tracker column ("In progress", "In review"...). Without columns, one phase per step.
 */
function phasesOf(steps: Step[], byState: Map<string, GraphNode>): Phase[] {
  const phases: Phase[] = [];
  for (const e of steps) {
    const column = byState.get(e.to)?.tracker_status ?? null;
    const last = phases.at(-1);
    if (column && last && last.label === column) {
      last.steps.push(e.id);
      continue;
    }
    phases.push({
      key: `${phases.length}`,
      label: column ?? byState.get(e.to)?.display ?? e.to,
      steps: [e.id],
    });
  }
  return phases;
}

// ───────────────────────── the journey's state at an instant ─────────────────────────

export type StepStatus = "upcoming" | "running" | "waiting" | "done" | "sent_back" | "failed";

export type StepState = {
  status: StepStatus;
  /** The actor's attempts on this transition, started before the instant. */
  attempts: JourneyStep[];
  rounds: number;
  verdict: string | null;
};

export type Instant = {
  t: number;
  /** The state the work item is in. */
  state: string;
  steps: Map<string, StepState>;
  /** The states of the path and of the detours the work item went through. */
  visited: Set<string>;
  /** How many times each arc was taken; `latest`: the most recent move is this one. */
  arcs: Map<string, { times: number; latest: boolean }>;
  /** The most recent move, to say it aloud during the replay. */
  latest: JourneyMove | null;
  /** The phase the work item is at (its index), or `null` if it is on none. */
  phase: number | null;
};

const ms = (iso: string | null | undefined) => (iso ? Date.parse(iso) : Number.NaN);
const WAITING = new Set(["waiting", "pending_approval"]);
const RUNNING = new Set([
  "queued",
  "running",
  "approved",
  "awaiting_evidence",
  "departing",
  "collecting",
  "deploying",
  "verifying",
  "staged",
]);
const FAILURES = new Set(["failed", "timed_out", "cancelled", "rejected", "rolled_back", "aborted"]);
const SENT_BACK = new Set(["reject", "changes_requested", "retry", "escalate"]);

function isEnd(model: JourneyModel, state: string): boolean {
  return state === model.path.at(-1) || model.exceptions.some((e) => e.id === state && e.terminal);
}

function runningAt(pas: JourneyStep, t: number): boolean {
  const ended = ms(pas.ended_at);
  if (!Number.isNaN(ended) && ended <= t) return false;
  // Ended after the instant: it was still running. Without an end: its status says whether it runs.
  if (!Number.isNaN(ended)) return true;
  return RUNNING.has(pas.status) || WAITING.has(pas.status);
}

export function atInstant(journey: Pick<WorkItemJourney, "moves" | "steps">, model: JourneyModel, t: number): Instant {
  const moves = journey.moves.filter((m) => ms(m.at) <= t);
  const latest = moves.at(-1) ?? null;
  const state = latest?.to ?? model.initial;
  const visited = new Set<string>([model.initial, ...moves.map((m) => m.to)]);

  const steps = new Map<string, StepState>();
  for (const step of model.byId.values()) {
    const attempts = journey.steps.filter((s) => s.transition_id === step.id && ms(s.started_at) <= t);
    const passes = moves.filter((m) => m.transition_id === step.id && m.kind === "nominal");
    const sentBack = moves.filter((m) => m.transition_id === step.id && SENT_BACK.has(m.kind));
    const lastPass = passes.length ? ms(passes.at(-1)!.at) : -Infinity;
    const lastSentBack = sentBack.length ? ms(sentBack.at(-1)!.at) : -Infinity;
    const running = attempts.filter((p) => runningAt(p, t));
    const last = attempts.at(-1);
    let status: StepStatus;
    if (running.length > 0) {
      status =
        step.kind === "human" || running.some((p) => WAITING.has(p.status) && Number.isNaN(ms(p.ended_at)))
          ? "waiting"
          : "running";
    } else if (state === step.from && !isEnd(model, state)) {
      // The work item is where this step starts: it is the next one to play, even if it was
      // already crossed (a review that sends back makes the tests run again). A platform
      // transition has no attempt of its own; neither does an agent between two attempts.
      status = step.kind === "human" ? "waiting" : "running";
    } else if (lastPass > lastSentBack && passes.length > 0) {
      status = "done";
    } else if (sentBack.length > 0) {
      status = "sent_back";
    } else if (last && FAILURES.has(last.status)) {
      status = "failed";
    } else {
      status = "upcoming";
    }
    const verdict = [...attempts].reverse().find((p) => p.verdict)?.verdict ?? null;
    steps.set(step.id, {
      status,
      attempts,
      rounds: attempts.length,
      verdict,
    });
  }

  // A work item at the end of the path: the last step is done, nothing is "next".
  const arcs = new Map<string, { times: number; latest: boolean }>();
  for (const arc of model.arcs) {
    const taken = moves.filter((m) => m.from === arc.from && m.to === arc.to);
    arcs.set(arc.key, {
      times: taken.length,
      latest: Boolean(latest && latest.from === arc.from && latest.to === arc.to),
    });
  }

  const phase = model.phases.findIndex((p) =>
    p.steps.some((id) => {
      const s = steps.get(id)?.status;
      return s === "running" || s === "waiting" || s === "sent_back" || s === "failed";
    }),
  );
  const lastDone = model.phases.findLastIndex((p) => p.steps.some((id) => steps.get(id)?.status === "done"));
  return {
    t,
    state,
    steps,
    visited,
    arcs,
    latest,
    phase: phase >= 0 ? phase : lastDone >= 0 ? lastDone : null,
  };
}

/** The replay's instants: every move, every start and every end of an attempt, in order. */
export function instants(journey: Pick<WorkItemJourney, "moves" | "steps">): number[] {
  const all = new Set<number>();
  for (const m of journey.moves) all.add(ms(m.at));
  for (const s of journey.steps) {
    all.add(ms(s.started_at));
    all.add(ms(s.ended_at));
  }
  return [...all].filter((t) => !Number.isNaN(t)).sort((a, b) => a - b);
}

/** The instant the replay jumps to for a phase: the first time one of its steps moved. */
export function phaseStart(journey: Pick<WorkItemJourney, "moves" | "steps">, model: JourneyModel, phase: Phase): number | null {
  const steps = new Set(phase.steps);
  const departs = new Set(phase.steps.map((id) => model.byId.get(id)?.from));
  const candidats = [
    ...journey.steps.filter((s) => s.transition_id && steps.has(s.transition_id)).map((s) => ms(s.started_at)),
    ...journey.moves.filter((m) => departs.has(m.to)).map((m) => ms(m.at)),
  ].filter((t) => !Number.isNaN(t));
  return candidats.length ? Math.min(...candidats) : null;
}

/** What the most recent arc tells, to announce it during the replay. */
export function narrate(model: JourneyModel, move: JourneyMove | null): string {
  if (!move) return "";
  const to = model.name(move.to);
  const who = move.transition_id ? model.byId.get(move.transition_id)?.label : undefined;
  switch (move.kind) {
    case "start":
      return `The work item arrives in ${to}.`;
    case "nominal":
      return who ? `${who} moved it to ${to}.` : `It moved to ${to}.`;
    case "changes_requested":
      return `${who ?? "A reviewer"} asked for changes: back to ${to}.`;
    case "reject":
      return `${who ?? "A person"} sent it back to ${to}.`;
    case "retry":
      return `${who ?? "The step"} failed: back to ${to} for another round.`;
    case "escalate":
      return `Retries ran out: ${to}.`;
    case "default":
      return `It went to ${to}.`;
    case "resume":
      return `A person answered: it resumes in ${to}.`;
    default:
      return `It moved to ${to}.`;
  }
}

/**
 * A workflow's map without a work item (S22-05): its structure, nothing crossed, nothing running.
 * Every step is "upcoming"; the map then colours it by who carries it.
 */
export function structure(model: JourneyModel): Instant {
  const steps = new Map<string, StepState>();
  for (const id of model.byId.keys()) steps.set(id, { status: "upcoming", attempts: [], rounds: 0, verdict: null });
  const arcs = new Map(model.arcs.map((a) => [a.key, { times: 0, latest: false }]));
  return { t: Number.NEGATIVE_INFINITY, state: "", steps, visited: new Set(), arcs, latest: null, phase: null };
}

/** Parked by an escalation or a question: the latest move led it there, and the work item is still alive.
 * "Replay the stage" brings it back to the step it left from (`rerun_stage`, #313). */
export function parked(journey: WorkItemJourney | undefined): boolean {
  const latest = journey?.moves.at(-1);
  return !!journey && !journey.closed && !!latest && ["escalate", "default"].includes(latest.kind);
}
