// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { formatElapsed } from "@/lib/activity";
import { API_BASE, api, IS_MOCK } from "@/lib/api";
import { cn } from "@/lib/cn";
import { usd } from "@/lib/format";
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
import type { WorkflowGraph, WorkItemDto } from "@/lib/types";
import { workflowHueClasses } from "@/lib/workflow-hue";

/** How long a card takes to slide to its new place. */
export const MOVE_MS = 450;
/** Without a stream, the items are read again this often. */
const POLL_MS = 5000;
/** One refetch for the events that arrive within this many milliseconds. */
const REFRESH_MS = 300;

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
function useFlip(
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

/**
 * The flow's state (S67-26's reduction), fed by the org activity stream and by re-reading the items.
 * In the demo there is no stream: a scripted story is played instead (`mocks/live.ts`).
 */
export function useLiveFlow({
  graph,
  items,
  org,
  slug,
  workflow,
  demo,
}: {
  graph: WorkflowGraph | undefined;
  items: WorkItemDto[];
  org: string;
  slug: string;
  workflow: string;
  demo: boolean;
}) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<LiveState>(() =>
    initialState(graph, demo ? [] : items, { day_start: startOfDay() }),
  );
  const [now, setNow] = useState(() => Date.now());
  const [connected, setConnected] = useState(demo);

  // A fresh read of the items: a card whose state changed moves.
  const seen = useRef(items);
  useEffect(() => {
    if (demo || seen.current === items) return;
    seen.current = items;
    setState((s) => applyItems(s, items, new Date().toISOString()));
  }, [items, demo]);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const refresh = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const reread = () => {
    clearTimeout(refresh.current);
    refresh.current = setTimeout(
      () => void queryClient.invalidateQueries({ queryKey: ["items", slug] }),
      REFRESH_MS,
    );
  };

  useEffect(() => {
    if (demo || typeof EventSource === "undefined") return;
    const source = new EventSource(
      `${API_BASE}${api.activityStreamPath(org, { project: slug, workflow })}`,
      {
        withCredentials: true,
      },
    );
    source.onopen = () => {
      setConnected(true);
      reread();
    };
    source.onerror = () => setConnected(false);
    const listeners = (["activity.upsert", "activity.ended"] as const).map(
      (name) => {
        const listener = (message: Event) => {
          let entry: LiveEntry;
          try {
            entry = JSON.parse(
              (message as MessageEvent<string>).data,
            ) as LiveEntry;
          } catch {
            return;
          }
          setState((s) => applyEvent(s, name, entry));
          if (name === "activity.ended") reread();
        };
        source.addEventListener(name, listener);
        return [name, listener] as const;
      },
    );
    return () => {
      clearTimeout(refresh.current);
      for (const [name, listener] of listeners)
        source.removeEventListener(name, listener);
      source.close();
      setConnected(false);
    };
    // `reread` only reads refs and the query client.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demo, org, slug, workflow]);

  // Without a stream, poll.
  useEffect(() => {
    if (demo || connected) return;
    const timer = setInterval(
      () => void queryClient.invalidateQueries({ queryKey: ["items", slug] }),
      POLL_MS,
    );
    return () => clearInterval(timer);
  }, [demo, connected, queryClient, slug]);

  // The demo's story, over and over (the fixtures are loaded on demand, never in a real build's path).
  useEffect(() => {
    if (!demo) return;
    let stopped = false;
    let timers: ReturnType<typeof setTimeout>[] = [];
    const play = async () => {
      const { liveScript } = await import("@/mocks/live");
      if (stopped) return;
      const fresh = liveScript(graph, workflow, Date.now());
      setState(initialState(graph, fresh.items, { day_start: startOfDay() }));
      timers = fresh.steps.map(({ after, step }) =>
        setTimeout(() => {
          setState((s) =>
            step.items
              ? applyItems(s, step.items, new Date().toISOString())
              : step.event && step.data
                ? applyEvent(s, step.event, step.data)
                : s,
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
  }, [demo, graph, workflow]);

  const flow = useMemo(
    () => view(state, new Date(now).toISOString()),
    [state, now],
  );
  const connection: Connection = connected ? "live" : "reconnecting";
  return { flow, connection };
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
      role="status"
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

function Ticker({
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
        wall ? "text-base" : "text-xs",
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

function Card({ card, wall }: { card: LiveCard; wall: boolean }) {
  const { running, failure, waiting } = card;
  const note = failure?.summary ? `${card.id}-why` : undefined;
  return (
    <li
      data-card-id={card.id}
      data-status={card.status}
      // A failed card takes focus so that its summary shows without a pointer (S67-27).
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
      tabIndex={failure ? 0 : undefined}
      aria-describedby={note}
      className={cn(
        "group relative rounded-md border bg-raised p-3 outline-none focus-visible:ring-2 focus-visible:ring-focus",
        card.status === "running" && "live-running border-running-line",
        card.status === "failed" && "border-failed-line bg-failed-soft",
        card.status === "waiting" && "border-waiting-line bg-waiting-soft",
        card.status === "idle" && "border-line",
        wall ? "space-y-2 p-4" : "space-y-1.5",
      )}
    >
      <div
        className={cn(
          "flex items-baseline justify-between gap-2 font-mono text-ink-muted",
          wall ? "text-base" : "text-xs",
        )}
      >
        <span data-verbatim>{card.key}</span>
        {card.status === "running" && (
          <span className="text-running-ink">running</span>
        )}
        {failure && (
          <span
            data-testid="failure-reason"
            className="font-sans font-medium text-failed-ink"
          >
            {failure.reason.replace(/_/g, " ")}
          </span>
        )}
        {waiting && (
          <span className="font-sans font-medium text-waiting-ink">
            waiting
          </span>
        )}
      </div>
      <p
        data-verbatim
        className={cn("leading-snug text-ink", wall ? "text-xl" : "text-sm")}
      >
        {card.title}
      </p>
      {running && (
        <p
          className={cn(
            "flex flex-wrap items-center gap-x-1.5 text-running-ink",
            wall ? "text-base" : "text-xs",
          )}
        >
          <span>{running.stage}</span>
          {running.agent && <span>· {running.agent}</span>}
          <span className="tabular-nums">
            · {formatElapsed(running.elapsed_ms)}
          </span>
          <span className="tabular-nums">· {usd(running.cost_usd)}</span>
        </p>
      )}
      {waiting && (
        <p className={cn("text-waiting-ink", wall ? "text-base" : "text-xs")}>
          Waiting for: <span data-verbatim>{waiting.label}</span>
        </p>
      )}
      {failure?.summary && (
        <p
          id={note}
          role="tooltip"
          className={cn(
            "absolute inset-x-2 top-full z-10 mt-1 hidden rounded-md border border-failed-line bg-surface p-2 text-ink shadow-lg group-focus:block group-hover:block",
            wall ? "text-base" : "text-xs",
          )}
        >
          {failure.summary}
        </p>
      )}
    </li>
  );
}

function Lane({
  lane,
  cards,
  name,
  wall,
}: {
  lane: LiveLane;
  cards: LiveCard[];
  name: string;
  wall: boolean;
}) {
  const hue = workflowHueClasses(name);
  return (
    <section
      data-lane={lane.state}
      aria-label={lane.display}
      className={cn(
        "flex min-h-0 flex-1 flex-col rounded-md border border-line bg-surface-muted",
        wall ? "min-w-72" : "min-w-56",
      )}
    >
      <div className={cn("h-1 rounded-t-md", hue.solid)} aria-hidden />
      <header
        className={cn(
          "flex items-baseline justify-between gap-2 px-3 pt-3 pb-2",
          hue.ink,
        )}
      >
        <h2
          data-verbatim
          className={cn("font-semibold", wall ? "text-xl" : "text-sm")}
        >
          {lane.display}
        </h2>
        <span
          data-testid="lane-count"
          className={cn(
            "rounded-full px-2 py-0.5 font-medium tabular-nums",
            hue.soft,
            wall ? "text-base" : "text-xs",
          )}
        >
          {lane.count}
        </span>
      </header>
      {(lane.left_today > 0 || lane.closed_today > 0) && (
        <p
          className={cn(
            "px-3 pb-2 text-ink-subtle",
            wall ? "text-base" : "text-xs",
          )}
        >
          {lane.closed_today > 0
            ? `${lane.closed_today} closed today`
            : `${lane.left_today} moved on today`}
        </p>
      )}
      <ul className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-2 pb-3">
        {cards.map((card) => (
          <Card key={card.id} card={card} wall={wall} />
        ))}
      </ul>
    </section>
  );
}

/** The lanes, their cards and the ticker: the whole live view, for a window or for the wall. */
export function LiveBoard({
  graph,
  items,
  org,
  slug,
  workflow,
  wall,
  demo = IS_MOCK,
  toolbar,
}: {
  graph: WorkflowGraph | undefined;
  items: WorkItemDto[];
  org: string;
  slug: string;
  workflow: string;
  wall: boolean;
  demo?: boolean;
  toolbar?: ReactNode;
}) {
  const { flow, connection } = useLiveFlow({
    graph,
    items,
    org,
    slug,
    workflow,
    demo,
  });
  const reduced = useReducedMotion();
  const lanesRef = useRef<HTMLDivElement>(null);
  const byId = new Map(flow.cards.map((card) => [card.id, card]));
  const signature = flow.lanes
    .map((lane) => `${lane.state}:${lane.cards.join(",")}`)
    .join("|");
  useFlip(lanesRef, signature, !reduced);
  const hue = workflowHueClasses(workflow);
  return (
    <div
      data-testid="live-board"
      data-wall={wall ? "true" : undefined}
      data-reduced-motion={reduced ? "true" : "false"}
      className={cn(
        "flex flex-col gap-3",
        wall ? "h-full p-6" : "h-[calc(100dvh-17rem)] min-h-[30rem]",
      )}
    >
      <div className="flex flex-wrap items-center gap-3">
        {/* In the console the page's own switcher says which workflow; on the wall nothing else does. */}
        {wall && (
        <span
          data-verbatim
          className={cn(
            "inline-flex items-center gap-2 rounded-full border px-3 py-1 font-medium",
            hue.pill,
            wall ? "text-lg" : "text-sm",
          )}
        >
          <span aria-hidden className={cn("size-2 rounded-full", hue.solid)} />
          {workflow}
        </span>
        )}
        <LiveIndicator connection={connection} wall={wall} />
        <span className="ml-auto flex items-center gap-2">{toolbar}</span>
      </div>
      <div ref={lanesRef} className="flex min-h-0 flex-1 gap-3 overflow-x-auto">
        {flow.lanes.map((lane) => (
          <Lane
            key={lane.state}
            lane={lane}
            cards={lane.cards.flatMap((id) => byId.get(id) ?? [])}
            name={workflow}
            wall={wall}
          />
        ))}
      </div>
      <Ticker entries={flow.ticker} lanes={flow.lanes} wall={wall} />
    </div>
  );
}

/**
 * The wall: the live view on the whole screen, taken out of the console's shell. The shell stays in the
 * page but is made `inert` (no focus, no click, hidden from assistive technology) while the wall is up.
 */
export function Wall({
  children,
  onExit,
}: {
  children: ReactNode;
  onExit: () => void;
}) {
  // False while the page is rendered on the server, where there is no `document` to portal into.
  const mounted = useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
  const node = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!mounted) return;
    const covered: HTMLElement[] = [];
    for (const el of Array.from(document.body.children)) {
      if (
        el instanceof HTMLElement &&
        el !== node.current &&
        !el.hasAttribute("inert")
      ) {
        el.setAttribute("inert", "");
        covered.push(el);
      }
    }
    const key = (e: KeyboardEvent) => e.key === "Escape" && onExit();
    window.addEventListener("keydown", key);
    document.documentElement.dataset.wall = "true";
    return () => {
      for (const el of covered) el.removeAttribute("inert");
      window.removeEventListener("keydown", key);
      delete document.documentElement.dataset.wall;
    };
  }, [mounted, onExit]);
  if (!mounted) return null;
  return createPortal(
    <div
      ref={node}
      data-testid="live-wall"
      className="fixed inset-0 z-50 overflow-hidden bg-surface-sunken text-ink"
    >
      {children}
    </div>,
    document.body,
  );
}
