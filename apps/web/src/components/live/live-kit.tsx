// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  useEffect,
  useLayoutEffect,
  useCallback,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import { API_BASE, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  applyEvent,
  applyItems,
  initialState,
  view,
  type LiveCard,
  type LiveEntry,
  type LiveLane,
  type LiveState,
  type TickerEntry,
} from "@/lib/live-flow";
import { type Currency } from "@/lib/spend";
import type { WorkflowGraph, WorkItemDto } from "@/lib/types";

/** How long a card takes to slide to its new place. */
export const MOVE_MS = 450;
/** Without a stream, the items are read again this often. */
const POLL_MS = 5000;
/** One refetch for the events that arrive within this many milliseconds. */
const REFRESH_MS = 300;
/** A card that has just arrived in a lane is lit for this long. */
export const ARRIVED_MS = 2500;

/**
 * An amount in the organisation's currency, the symbol only (`€0.02`, `$0.02`). The stream carries dollars;
 * `usdTo` is the ledger's own rate (the cost report's `cost_eur` over its `cost_usd`), `null` when it has
 * none yet, and the amount is then told in dollars rather than converted with a guess.
 */
export function liveMoney(
  usd: number | null | undefined,
  currency: Currency,
  usdTo: number | null,
): string {
  if (usd === null || usd === undefined) return "—";
  const inEuro = currency === "EUR" && usdTo !== null;
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: inEuro ? "EUR" : "USD",
    currencyDisplay: "narrowSymbol",
    minimumFractionDigits: 2,
    maximumFractionDigits: usd !== 0 && Math.abs(usd) < 0.01 ? 4 : 2,
  }).format(inEuro ? usd * (usdTo ?? 1) : usd);
}

/** The start of the local day: what "left today" and "closed today" count from. */
function startOfDay(): string {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const read = () => setReduced(query.matches);
    read();
    query.addEventListener?.("change", read);
    return () => query.removeEventListener?.("change", read);
  }, []);
  return reduced;
}

/**
 * FLIP with CSS transforms and no library: after a layout change, each card that is not where it was is
 * put back where it was (an inverse transform, with no transition), then released to slide to its place.
 * `signature` changes when a card changes lane or order; without motion, nothing is applied at all.
 */
export function useFlip(
  root: RefObject<HTMLElement | null>,
  signature: string,
  enabled: boolean,
) {
  const rects = useRef(new Map<string, DOMRect>());
  const last = useRef<string | null>(null);
  useLayoutEffect(() => {
    const container = root.current;
    if (!container) return;
    const cards = Array.from(
      container.querySelectorAll<HTMLElement>("[data-card-id]"),
    );
    const next = new Map<string, DOMRect>();
    for (const el of cards)
      next.set(el.dataset.cardId ?? "", el.getBoundingClientRect());
    if (enabled && last.current !== null && last.current !== signature) {
      for (const el of cards) {
        const before = rects.current.get(el.dataset.cardId ?? "");
        const after = next.get(el.dataset.cardId ?? "");
        if (!before || !after) continue;
        const dx = before.left - after.left;
        const dy = before.top - after.top;
        if (!dx && !dy) continue;
        el.style.transition = "none";
        el.style.transform = `translate(${dx}px, ${dy}px)`;
        void el.offsetWidth;
        el.style.transition = `transform ${MOVE_MS}ms cubic-bezier(0.22, 1, 0.36, 1)`;
        el.style.transform = "";
      }
    }
    last.current = signature;
    rects.current = next;
  });
}

export type Connection = "live" | "reconnecting";

type Listener = (name: "activity.upsert" | "activity.ended", entry: LiveEntry) => void;

/** The organisation's activity stream, read once for the project and handed to every workflow on the page. */
export interface Feed {
  connection: Connection;
  subscribe: (listener: Listener) => () => void;
}

/**
 * One `EventSource` per page (ADR 0187 §4), filtered by project: each workflow's reduction takes the
 * events of its own items and ignores the rest. A finished run re-reads the items (the cards move
 * when the items say so); without a stream, the items are polled. In the demo there is no stream.
 */
export function useActivityFeed({
  org,
  slug,
  demo,
}: {
  org: string;
  slug: string;
  demo: boolean;
}): Feed {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(demo);
  const listeners = useRef(new Set<Listener>());
  const refresh = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const subscribe = useCallback((listener: Listener) => {
    listeners.current.add(listener);
    return () => void listeners.current.delete(listener);
  }, []);

  useEffect(() => {
    if (demo || typeof EventSource === "undefined") return;
    const reread = () => {
      clearTimeout(refresh.current);
      refresh.current = setTimeout(
        () => void queryClient.invalidateQueries({ queryKey: ["items", slug] }),
        REFRESH_MS,
      );
    };
    const source = new EventSource(
      `${API_BASE}${api.activityStreamPath(org, { project: slug })}`,
      { withCredentials: true },
    );
    source.onopen = () => {
      setConnected(true);
      reread();
    };
    source.onerror = () => setConnected(false);
    const attached = (["activity.upsert", "activity.ended"] as const).map((name) => {
      const listener = (message: Event) => {
        let entry: LiveEntry;
        try {
          entry = JSON.parse((message as MessageEvent<string>).data) as LiveEntry;
        } catch {
          return;
        }
        for (const fn of listeners.current) fn(name, entry);
        if (name === "activity.ended") reread();
      };
      source.addEventListener(name, listener);
      return [name, listener] as const;
    });
    return () => {
      clearTimeout(refresh.current);
      for (const [name, listener] of attached) source.removeEventListener(name, listener);
      source.close();
      setConnected(false);
    };
  }, [demo, org, slug, queryClient]);

  // Without a stream, poll.
  useEffect(() => {
    if (demo || connected) return;
    const timer = setInterval(
      () => void queryClient.invalidateQueries({ queryKey: ["items", slug] }),
      POLL_MS,
    );
    return () => clearInterval(timer);
  }, [demo, connected, queryClient, slug]);

  return { connection: connected ? "live" : "reconnecting", subscribe };
}

/** A clock that ticks every second: what the running cards' elapsed time reads. */
export function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

interface Box {
  state: LiveState;
  arrived: Record<string, number>;
  /** The demo's items as its script has moved them. */
  played: WorkItemDto[];
  graph: WorkflowGraph | undefined;
}

/**
 * One workflow's flow (S67-26's reduction), fed by the page's activity feed and by re-reading the items.
 * In the demo there is no stream: a scripted story is played instead (`mocks/live.ts`), and the items it
 * plays are the ones the board shows (`items`).
 */
export function useLiveFlow({
  graph,
  items,
  workflow,
  feed,
  demo,
}: {
  graph: WorkflowGraph | undefined;
  items: WorkItemDto[];
  workflow: string;
  feed: Feed;
  demo: boolean;
}) {
  const start = (): Box => ({
    state: initialState(graph, demo ? [] : items, { day_start: startOfDay() }),
    arrived: {},
    played: [],
    graph,
  });
  const [box, setBox] = useState<Box>(start);
  // The validator answers after the page has rendered: the flow starts again on the graph it read.
  if (box.graph !== graph) setBox(start());
  // Every change goes through here: a card whose lane changed is remembered as just arrived.
  const setState = useCallback((next: (s: LiveState) => LiveState, played?: WorkItemDto[]) => {
    setBox((b) => {
      const state = next(b.state);
      if (state === b.state && !played) return b;
      const before = new Map(b.state.slots.map((slot) => [slot.id, slot.lane]));
      const arrived = { ...b.arrived };
      for (const slot of state.slots) {
        const was = before.get(slot.id);
        if (was !== undefined && was !== slot.lane) arrived[slot.id] = Date.now();
      }
      return { state, arrived, played: played ?? b.played, graph: b.graph };
    });
  }, []);
  const now = useNow();

  // A fresh read of the items: a card whose state changed moves.
  const seen = useRef(items);
  useEffect(() => {
    if (demo || seen.current === items) return;
    seen.current = items;
    setState((s) => applyItems(s, items, new Date().toISOString()));
  }, [items, demo, setState]);

  useEffect(
    () => feed.subscribe((name, entry) => setState((s) => applyEvent(s, name, entry))),
    [feed, setState],
  );

  // The demo's story, over and over (the fixtures are loaded on demand, never in a real build's path).
  useEffect(() => {
    if (!demo) return;
    let stopped = false;
    let timers: ReturnType<typeof setTimeout>[] = [];
    const play = async () => {
      const { liveScript } = await import("@/mocks/live");
      if (stopped) return;
      const fresh = liveScript(graph, workflow, Date.now());
      setBox({
        state: initialState(graph, fresh.items, { day_start: startOfDay() }),
        arrived: {},
        played: fresh.items,
        graph,
      });
      timers = fresh.steps.map(({ after, step }) =>
        setTimeout(() => {
          setState(
            (s) =>
              step.items
                ? applyItems(s, step.items, new Date().toISOString())
                : step.event && step.data
                  ? applyEvent(s, step.event, step.data)
                  : s,
            step.items,
          );
        }, after),
      );
      timers.push(setTimeout(() => void play(), fresh.restartAfter));
    };
    void play();
    return () => {
      stopped = true;
      timers.forEach(clearTimeout);
    };
  }, [demo, graph, workflow, setState]);

  const flow = useMemo(() => view(box.state, new Date(now).toISOString()), [box.state, now]);
  const arrived = useMemo(
    () =>
      new Set(
        Object.entries(box.arrived)
          .filter(([, at]) => now - at < ARRIVED_MS)
          .map(([id]) => id),
      ),
    [box.arrived, now],
  );
  // The demo's tickets come on top of the ones the mock API has, so that nothing of the demo goes missing.
  return { flow, arrived, now, items: demo ? [...items, ...box.played] : items };
}

export function LiveIndicator({
  connection,
  wall,
}: {
  connection: Connection;
  wall: boolean;
}) {
  const live = connection === "live";
  return (
    <span
      data-testid="live-indicator"
      data-state={connection}
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-3 py-1 font-medium tracking-wide",
        wall ? "text-base" : "text-xs",
        live
          ? "border-succeeded-line bg-succeeded-soft text-succeeded-ink"
          : "border-waiting-line bg-waiting-soft text-waiting-ink",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "size-2 rounded-full",
          live ? "live-dot bg-succeeded" : "bg-waiting",
        )}
      />
      {live ? "LIVE" : "RECONNECTING"}
    </span>
  );
}

function laneName(lanes: LiveLane[], state: string | null): string {
  return lanes.find((lane) => lane.state === state)?.display ?? state ?? "";
}

function tickerText(entry: TickerEntry, lanes: LiveLane[]): string {
  const stage = entry.stage ? ` ${entry.stage}` : "";
  switch (entry.kind) {
    case "started":
      return `started${stage}`;
    case "succeeded":
      return `finished${stage}`;
    case "failed":
      return `failed${stage}${entry.reason ? ` (${entry.reason.replace(/_/g, " ")})` : ""}`;
    case "cancelled":
      return `cancelled${stage}`;
    case "moved":
      return `moved ${laneName(lanes, entry.from)} to ${laneName(lanes, entry.to)}`;
    case "decided":
      return `${entry.reason ?? "request"} decided`;
  }
}

const TICKER_TONE: Record<TickerEntry["kind"], string> = {
  started: "bg-running",
  succeeded: "bg-succeeded",
  failed: "bg-failed",
  cancelled: "bg-neutral",
  moved: "bg-accent",
  decided: "bg-waiting",
};

export function Ticker({
  entries,
  lanes,
  wall,
}: {
  entries: TickerEntry[];
  lanes: LiveLane[];
  wall: boolean;
}) {
  return (
    <ol
      role="log"
      aria-label="Latest events"
      data-testid="live-ticker"
      className={cn(
        "flex min-h-10 items-center gap-6 overflow-hidden rounded-md border border-line bg-surface px-4 py-2 whitespace-nowrap",
        wall ? "text-xl" : "text-xs",
      )}
    >
      {entries.length === 0 && (
        <li className="text-ink-subtle">Waiting for the first event.</li>
      )}
      {entries.map((entry, index) => (
        <li
          key={`${entry.at}-${entry.key}-${entry.kind}-${index}`}
          className={cn(
            "flex items-center gap-2",
            index > 0 && "text-ink-muted",
          )}
        >
          <span
            aria-hidden
            className={cn("size-1.5 rounded-full", TICKER_TONE[entry.kind])}
          />
          <span className="font-mono text-ink-subtle">
            {new Date(entry.at).toLocaleTimeString("en-GB", {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            })}
          </span>
          <span className="font-mono">{entry.key}</span>
          <span>{tickerText(entry, lanes)}</span>
        </li>
      ))}
    </ol>
  );
}

/** The agent is told only when it is not the stage's own name; "developer · developer" says nothing. */
export function stageLine(stage: string, agent: string | null): string {
  return agent && agent.trim().toLowerCase() !== stage.trim().toLowerCase()
    ? `${stage} · ${agent}`
    : stage;
}

function Figure({
  label,
  value,
  tone,
  testid,
  wall,
}: {
  label: string;
  value: string;
  tone?: string;
  testid: string;
  wall: boolean;
}) {
  return (
    <div className="flex items-baseline gap-2" data-testid={testid}>
      <dd className={cn("font-semibold tabular-nums", tone, wall ? "text-3xl" : "text-lg")}>
        {value}
      </dd>
      <dt className={cn("text-ink-muted", wall ? "text-base" : "text-xs")}>{label}</dt>
    </div>
  );
}

/** A slim line of what the flow is doing now: in flight, waiting for a person, failed, done today, spend today. */
export function Summary({
  flow,
  spent,
  wall,
}: {
  flow: ReturnType<typeof view>;
  spent: string | null;
  wall: boolean;
}) {
  const count = (status: LiveCard["status"]) =>
    flow.cards.filter((card) => card.status === status).length;
  const done = flow.lanes.reduce((sum, lane) => sum + lane.closed_today, 0);
  return (
    <dl
      data-testid="live-summary"
      aria-label="Summary"
      className={cn(
        "flex flex-wrap items-baseline rounded-md border border-line bg-surface px-4 py-2",
        wall ? "gap-x-10 gap-y-2" : "gap-x-8 gap-y-1",
      )}
    >
      <Figure testid="sum-running" label="In flight" value={String(count("running"))} tone="text-running-ink" wall={wall} />
      <Figure testid="sum-waiting" label="Waiting for a person" value={String(count("waiting"))} tone="text-waiting-ink" wall={wall} />
      <Figure testid="sum-failed" label="Failed" value={String(count("failed"))} tone="text-failed-ink" wall={wall} />
      <Figure testid="sum-done" label="Done today" value={String(done)} tone="text-succeeded-ink" wall={wall} />
      {spent !== null && <Figure testid="sum-spend" label="Spent today" value={spent} wall={wall} />}
    </dl>
  );
}

