// SPDX-License-Identifier: Apache-2.0
/**
 * The live flow's reduction (S67-26, ADR 0187 §4): (workflow graph, work items, activity stream events)
 * to (lanes, cards, ticker). Pure: no clock, no network, no React. `packages/cli/src/tranibor_cli/live_flow.py`
 * is the same function written in Python, and both are tested against the fixtures of
 * `packages/contracts/fixtures/live/`, so the web and the terminal cannot show a different flow for
 * the same events. A change here is a change there, and a fixture that shows it.
 *
 * Output keys are snake_case, as the fixtures write them.
 */
import { columnsFromGraph } from "@/components/workflows/board";
import type { ActivityEntry, WorkflowGraph, WorkItemDto } from "./types";

/** What the stream's `ActivityEntry` may carry beyond the contract this reduction was written on (S67-19). */
export type LiveEntry = ActivityEntry & {
  failure?: { reason: string; summary?: string } | null;
};

export type CardStatus = "idle" | "running" | "failed" | "waiting";

export interface LiveCard {
  id: string;
  key: string;
  title: string;
  lane: string;
  status: CardStatus;
  running: {
    run_id: string;
    stage: string;
    agent: string | null;
    started_at: string | null;
    elapsed_ms: number;
    cost_usd: number | null;
  } | null;
  failure: { reason: string; summary: string } | null;
  waiting: { kind: string; label: string } | null;
  /** The last move this card made (a re-read saw its state change), with the graph edge it took; null when it has not moved. */
  transition: Transition | null;
}

/** A card's last move: the state it left, the state it entered and the graph edge between them (null when the graph has none). */
export interface Transition {
  from: string;
  to: string;
  edge: string | null;
}

export interface LiveLane {
  state: string;
  display: string;
  kind: string | null;
  terminal: boolean;
  hors_workflow: boolean;
  count: number;
  left_today: number;
  closed_today: number;
  cards: string[];
}

export type TickerKind =
  "started" | "succeeded" | "failed" | "cancelled" | "moved" | "decided";

export interface TickerEntry {
  at: string;
  kind: TickerKind;
  key: string;
  stage: string | null;
  from: string | null;
  to: string | null;
  reason: string | null;
}

export interface LiveView {
  lanes: LiveLane[];
  cards: LiveCard[];
  ticker: TickerEntry[];
}

export interface LiveOptions {
  /** How many events the ticker keeps. */
  ticker_size?: number;
  /** The start of the day (ISO) that `left_today` and `closed_today` count from. */
  day_start?: string | null;
}

export const DEFAULT_TICKER_SIZE = 12;
/** A failure's summary is cut to this many characters, as the API cuts it (ADR 0187 §3). */
export const SUMMARY_MAX = 300;
/** What a card says it waits for stays on one line. */
export const WAITING_LABEL_MAX = 80;

const IN_FLIGHT = new Set(["queued", "running"]);
const FAILED = new Set(["failed", "timed_out"]);

interface Run {
  run_id: string;
  stage: string;
  agent: string | null;
  started_at: string | null;
  cost_usd: number | null;
}

interface Slot {
  id: string;
  key: string;
  title: string;
  lane: string;
  lane_display: string | null;
  run: Run | null;
  /** A failure the stream reported, kept until a run starts, a decision is taken or the card moves. */
  stream_failure: { reason: string; summary: string } | null;
  item_failure: { reason: string; summary: string } | null;
  waiting: { kind: string; label: string } | null;
  last_move: Transition | null;
}

export interface LiveState {
  graph: WorkflowGraph | undefined;
  slots: Slot[];
  /** The last status seen of each run, so that a replayed event does not repeat in the ticker. */
  seen: Map<string, string>;
  /** Moves out of a lane observed today, by lane. */
  left: Map<string, number>;
  closed: Map<string, number>;
  ticker: TickerEntry[];
  options: Required<LiveOptions>;
}

export function cut(text: string | null | undefined, max: number): string {
  const flat = (text ?? "").replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

function firstLine(text: string): string {
  return text.split("\n").find((line) => line.trim() !== "") ?? "";
}

function waitingOf(item: WorkItemDto): Slot["waiting"] {
  const request = item.pending_request;
  if (!request || request.decided_at) return null;
  const payload = (request.payload ?? {}) as Record<string, unknown>;
  const text = [payload.title, payload.question, payload.summary].find(
    (v) => typeof v === "string" && v.trim() !== "",
  );
  return {
    kind: request.kind,
    label: cut(
      firstLine(typeof text === "string" ? text : request.kind),
      WAITING_LABEL_MAX,
    ),
  };
}

function itemFailureOf(item: WorkItemDto): Slot["item_failure"] {
  if (item.failure)
    return {
      reason: "workflow",
      summary: cut(item.failure.message, SUMMARY_MAX),
    };
  const run = item.current_run;
  if (run && FAILED.has(run.status)) return { reason: run.status, summary: "" };
  return null;
}

function runOfItem(item: WorkItemDto): Run | null {
  const run = item.current_run;
  if (!run || !IN_FLIGHT.has(run.status)) return null;
  return {
    run_id: run.id,
    stage: run.stage_role,
    agent: null,
    started_at: run.started_at ?? null,
    cost_usd: run.cost_usd ?? null,
  };
}

/** Whether `iso` falls on or after the state's day start (no day start: nothing counts as today). */
function today(state: LiveState, iso: string | null | undefined): boolean {
  const start = state.options.day_start;
  return Boolean(start && iso && Date.parse(iso) >= Date.parse(start));
}

export function initialState(
  graph: WorkflowGraph | undefined,
  items: WorkItemDto[],
  options: LiveOptions = {},
): LiveState {
  const state: LiveState = {
    graph,
    slots: [],
    seen: new Map(),
    left: new Map(),
    closed: new Map(),
    ticker: [],
    options: {
      ticker_size: options.ticker_size ?? DEFAULT_TICKER_SIZE,
      day_start: options.day_start ?? null,
    },
  };
  for (const item of items) {
    state.slots.push({
      id: item.id,
      key: item.tracker_key,
      title: item.title,
      lane: item.state,
      lane_display: item.state_display ?? null,
      run: runOfItem(item),
      stream_failure: null,
      item_failure: itemFailureOf(item),
      waiting: waitingOf(item),
      last_move: null,
    });
    if (item.current_run)
      state.seen.set(item.current_run.id, item.current_run.status);
    if (item.closed_at && today(state, item.closed_at))
      state.closed.set(item.state, (state.closed.get(item.state) ?? 0) + 1);
  }
  return state;
}

/** The id of the graph edge from one state to another (`from->to` when the edge has no id), null when the graph has none. */
export function edgeBetween(
  graph: WorkflowGraph | undefined,
  from: string,
  to: string,
): string | null {
  const edges = (graph?.edges ?? []) as { id?: string; from: string; to: string }[];
  const edge = edges.find((e) => e.from === from && e.to === to);
  return edge ? (edge.id ?? `${from}->${to}`) : null;
}

function push(state: LiveState, entry: TickerEntry): void {
  state.ticker = [entry, ...state.ticker].slice(0, state.options.ticker_size);
}

/**
 * A fresh read of the work items (what the page does on `activity.ended`): a card whose state changed
 * moves to the end of its new lane; an item that appeared is added; one that vanished is dropped.
 * `at` stamps what the move writes in the ticker.
 */
export function applyItems(
  state: LiveState,
  items: WorkItemDto[],
  at: string,
): LiveState {
  const next: LiveState = {
    ...state,
    slots: [],
    seen: new Map(state.seen),
    left: new Map(state.left),
    closed: new Map(state.closed),
    ticker: state.ticker,
  };
  const known = new Map(state.slots.map((slot) => [slot.id, slot]));
  const fresh: Slot[] = [];
  const moved: Slot[] = [];
  for (const item of items) {
    const before = known.get(item.id);
    const waiting = waitingOf(item);
    const snapshotRun = runOfItem(item);
    const ended =
      item.current_run && !IN_FLIGHT.has(item.current_run.status)
        ? item.current_run.id
        : null;
    if (item.current_run) {
      const seen = next.seen.get(item.current_run.id);
      if (!seen || IN_FLIGHT.has(item.current_run.status))
        next.seen.set(item.current_run.id, item.current_run.status);
    }
    if (!before) {
      fresh.push({
        id: item.id,
        key: item.tracker_key,
        title: item.title,
        lane: item.state,
        lane_display: item.state_display ?? null,
        run: snapshotRun,
        stream_failure: null,
        item_failure: itemFailureOf(item),
        waiting,
        last_move: null,
      });
      continue;
    }
    const slot: Slot = {
      ...before,
      title: item.title,
      key: item.tracker_key,
      lane_display: item.state_display ?? null,
      waiting,
      item_failure: itemFailureOf(item),
    };
    if (snapshotRun && slot.run?.run_id !== snapshotRun.run_id) {
      slot.run = snapshotRun;
      slot.stream_failure = null;
    } else if (slot.run && ended === slot.run.run_id) {
      slot.run = null;
    }
    if (before.waiting && !waiting) {
      slot.stream_failure = null;
      push(next, {
        at,
        kind: "decided",
        key: slot.key,
        stage: null,
        from: null,
        to: null,
        reason: before.waiting.kind,
      });
    }
    if (before.lane !== item.state) {
      push(next, {
        at,
        kind: "moved",
        key: slot.key,
        stage: null,
        from: before.lane,
        to: item.state,
        reason: null,
      });
      if (today(state, at))
        next.left.set(before.lane, (next.left.get(before.lane) ?? 0) + 1);
      slot.lane = item.state;
      slot.last_move = {
        from: before.lane,
        to: item.state,
        edge: edgeBetween(state.graph, before.lane, item.state),
      };
      slot.stream_failure = null;
      moved.push(slot);
      continue;
    }
    fresh.push(slot);
  }
  // Keep the order of the previous cards, then the moved ones and the new ones at the end.
  const order = new Map(state.slots.map((slot, index) => [slot.id, index]));
  const stay = fresh
    .filter((slot) => order.has(slot.id))
    .sort((a, b) => (order.get(a.id) ?? 0) - (order.get(b.id) ?? 0));
  const added = fresh.filter((slot) => !order.has(slot.id));
  next.slots = [...stay, ...moved, ...added];
  return next;
}

/** One `activity.upsert` or `activity.ended` message; anything else, or an entry that is not a run of a known item, changes nothing. */
export function applyEvent(
  state: LiveState,
  event: string,
  entry: LiveEntry,
): LiveState {
  if (event !== "activity.upsert" && event !== "activity.ended") return state;
  if (entry.kind !== "run" || !entry.work_item_id) return state;
  const index = state.slots.findIndex((slot) => slot.id === entry.work_item_id);
  if (index < 0) return state;
  const slot = state.slots[index];
  if (!slot) return state;
  const next: LiveState = {
    ...state,
    slots: [...state.slots],
    seen: new Map(state.seen),
    ticker: state.ticker,
  };
  const previous = state.seen.get(entry.id);
  const stage = entry.stage_role ?? null;
  if (IN_FLIGHT.has(entry.status) && event === "activity.upsert") {
    next.seen.set(entry.id, entry.status);
    next.slots[index] = {
      ...slot,
      run: {
        run_id: entry.id,
        stage: stage ?? slot.run?.stage ?? "run",
        agent: entry.agent_slug ?? slot.run?.agent ?? null,
        started_at: entry.started_at,
        cost_usd: entry.spend
          ? entry.spend.cost_usd
          : (slot.run?.cost_usd ?? null),
      },
      stream_failure: null,
    };
    if (!previous)
      push(next, {
        at: entry.started_at,
        kind: "started",
        key: slot.key,
        stage,
        from: null,
        to: null,
        reason: null,
      });
    return next;
  }
  if (IN_FLIGHT.has(entry.status)) return state;
  // An ended entry. A replay of one already seen changes nothing.
  if (previous === entry.status && previous && !IN_FLIGHT.has(previous))
    return state;
  next.seen.set(entry.id, entry.status);
  const at = entry.ended_at ?? entry.started_at;
  const failed = FAILED.has(entry.status);
  const run = slot.run && slot.run.run_id === entry.id ? null : slot.run;
  next.slots[index] = {
    ...slot,
    run,
    stream_failure: failed
      ? {
          reason: entry.failure?.reason ?? entry.status,
          summary: cut(entry.failure?.summary, SUMMARY_MAX),
        }
      : slot.stream_failure,
  };
  const kind: TickerKind = failed
    ? "failed"
    : entry.status === "cancelled"
      ? "cancelled"
      : "succeeded";
  push(next, {
    at,
    kind,
    key: slot.key,
    stage,
    from: null,
    to: null,
    reason: failed ? (entry.failure?.reason ?? entry.status) : null,
  });
  return next;
}

/** The view at `now` (ISO): lanes with their counts, cards with the elapsed time of a running one, the ticker. */
export function view(state: LiveState, now: string): LiveView {
  const nowMs = Date.parse(now);
  const columns = columnsFromGraph(
    state.graph,
    state.slots.map(
      (slot) =>
        ({
          state: slot.lane,
          state_display: slot.lane_display,
        }) as unknown as WorkItemDto,
    ),
  );
  const cards: LiveCard[] = state.slots.map((slot) => {
    const failure = slot.run
      ? null
      : (slot.stream_failure ?? slot.item_failure);
    const status: CardStatus = slot.run
      ? "running"
      : failure
        ? "failed"
        : slot.waiting
          ? "waiting"
          : "idle";
    const started = slot.run?.started_at
      ? Date.parse(slot.run.started_at)
      : NaN;
    return {
      id: slot.id,
      key: slot.key,
      title: slot.title,
      lane: slot.lane,
      status,
      running: slot.run
        ? {
            run_id: slot.run.run_id,
            stage: slot.run.stage,
            agent: slot.run.agent,
            started_at: slot.run.started_at,
            elapsed_ms: Number.isNaN(started)
              ? 0
              : Math.max(0, nowMs - started),
            cost_usd: slot.run.cost_usd,
          }
        : null,
      failure,
      waiting: slot.waiting,
      transition: slot.last_move,
    };
  });
  const lanes: LiveLane[] = columns.map((column) => {
    const inLane = cards.filter((card) => card.lane === column.state);
    return {
      state: column.state,
      display: column.display,
      kind: column.kind ?? null,
      terminal: Boolean(column.terminal),
      hors_workflow: Boolean(column.horsWorkflow),
      count: inLane.length,
      left_today: state.left.get(column.state) ?? 0,
      closed_today: state.closed.get(column.state) ?? 0,
      cards: inLane.map((card) => card.id),
    };
  });
  return { lanes, cards, ticker: state.ticker };
}

/** One call for a fixture: the state after `items`, each of the `steps`, viewed at `now`. */
export interface LiveStep {
  at?: string;
  items?: WorkItemDto[];
  event?: string;
  data?: LiveEntry;
}

export function reduce(
  graph: WorkflowGraph | undefined,
  items: WorkItemDto[],
  steps: LiveStep[],
  now: string,
  options: LiveOptions = {},
): LiveView {
  let state = initialState(graph, items, options);
  for (const step of steps) {
    if (step.items) state = applyItems(state, step.items, step.at ?? now);
    else if (step.event && step.data)
      state = applyEvent(state, step.event, step.data);
  }
  return view(state, now);
}
