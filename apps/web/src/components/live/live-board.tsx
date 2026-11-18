// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  type CSSProperties,
  useEffect,
  useLayoutEffect,
  useCallback,
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
import { workflowHue, workflowHueClasses } from "@/lib/workflow-hue";

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
  const [box, setBox] = useState<{
    state: LiveState;
    arrived: Record<string, number>;
  }>(() => ({
    state: initialState(graph, demo ? [] : items, { day_start: startOfDay() }),
    arrived: {},
  }));
  // Every change goes through here: a card whose lane changed is remembered as just arrived.
  const setState = useCallback((next: (s: LiveState) => LiveState) => {
    setBox((b) => {
      const state = next(b.state);
      if (state === b.state) return b;
      const before = new Map(b.state.slots.map((slot) => [slot.id, slot.lane]));
      const arrived = { ...b.arrived };
      for (const slot of state.slots) {
        const was = before.get(slot.id);
        if (was !== undefined && was !== slot.lane) arrived[slot.id] = Date.now();
      }
      return { state, arrived };
    });
  }, []);
  const [now, setNow] = useState(() => Date.now());
  const [connected, setConnected] = useState(demo);

  // A fresh read of the items: a card whose state changed moves.
  const seen = useRef(items);
  useEffect(() => {
    if (demo || seen.current === items) return;
    seen.current = items;
    setState((s) => applyItems(s, items, new Date().toISOString()));
  }, [items, demo, setState]);

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
    // `reread` only reads refs and the query client; `setState` is stable.
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
      setBox({
        state: initialState(graph, fresh.items, { day_start: startOfDay() }),
        arrived: {},
      });
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
  }, [demo, graph, workflow, setState]);

  const flow = useMemo(
    () => view(box.state, new Date(now).toISOString()),
    [box.state, now],
  );
  const arrived = useMemo(
    () =>
      new Set(
        Object.entries(box.arrived)
          .filter(([, at]) => now - at < ARRIVED_MS)
          .map(([id]) => id),
      ),
    [box.arrived, now],
  );
  const connection: Connection = connected ? "live" : "reconnecting";
  return { flow, connection, arrived, now };
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

function Card({
  card,
  wall,
  hue,
  arrived,
  format,
}: {
  card: LiveCard;
  wall: boolean;
  hue: number;
  arrived: boolean;
  format: (usd: number | null | undefined) => string;
}) {
  const { running, failure, waiting } = card;
  const note = failure?.summary ? `${card.id}-why` : undefined;
  return (
    <li
      data-card-id={card.id}
      data-status={card.status}
      data-arrived={arrived ? "true" : undefined}
      style={{ "--live-hue": `var(--choregos-cat-${hue}-solid)` } as CSSProperties}
      // A failed card takes focus so that its summary shows without a pointer (S67-27).
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
      tabIndex={failure ? 0 : undefined}
      aria-describedby={note}
      className={cn(
        "group relative rounded-md border bg-raised p-3 outline-none focus-visible:ring-2 focus-visible:ring-focus",
        arrived && "live-arrived",
        card.status === "running" && "live-running overflow-hidden border-running-line",
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
          <span data-testid="stage-line">
            {stageLine(running.stage, running.agent)}
          </span>
          <span className="tabular-nums">
            · {formatElapsed(running.elapsed_ms)}
          </span>
          <span className="tabular-nums">· {format(running.cost_usd)}</span>
        </p>
      )}
      {running && <span aria-hidden className="live-progress" />}
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
  arrived,
  format,
}: {
  lane: LiveLane;
  cards: LiveCard[];
  name: string;
  wall: boolean;
  arrived: Set<string>;
  format: (usd: number | null | undefined) => string;
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
          <Card
            key={card.id}
            card={card}
            wall={wall}
            hue={workflowHue(name)}
            arrived={arrived.has(card.id)}
            format={format}
          />
        ))}
      </ul>
    </section>
  );
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
function Summary({
  flow,
  spent,
  wall,
}: {
  flow: ReturnType<typeof view>;
  spent: string;
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
      <Figure testid="sum-running" label="in flight" value={String(count("running"))} tone="text-running-ink" wall={wall} />
      <Figure testid="sum-waiting" label="waiting for a person" value={String(count("waiting"))} tone="text-waiting-ink" wall={wall} />
      <Figure testid="sum-failed" label="failed" value={String(count("failed"))} tone="text-failed-ink" wall={wall} />
      <Figure testid="sum-done" label="done today" value={String(done)} tone="text-succeeded-ink" wall={wall} />
      <Figure testid="sum-spend" label="spent today" value={spent} wall={wall} />
    </dl>
  );
}

/** The wall's clock, top right: the time of the last tick. */
function Clock({ now }: { now: number }) {
  return (
    <time
      data-testid="live-clock"
      dateTime={new Date(now).toISOString()}
      className="font-mono text-4xl tabular-nums text-ink-muted"
    >
      {new Date(now).toLocaleTimeString("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
      })}
    </time>
  );
}

/**
 * The lanes, their cards and the ticker: the whole live view, for a window or for the wall.
 * `switcher` is the one row's left side (the workflow chips); LIVE sits at its right, then the toolbar.
 */
export function LiveBoard({
  graph,
  items,
  org,
  slug,
  workflow,
  wall,
  demo = IS_MOCK,
  toolbar,
  switcher,
  currency = "USD",
  usdTo = null,
  spentTodayUsd = null,
}: {
  graph: WorkflowGraph | undefined;
  items: WorkItemDto[];
  org: string;
  slug: string;
  workflow: string;
  wall: boolean;
  demo?: boolean;
  toolbar?: ReactNode;
  switcher?: ReactNode;
  currency?: Currency;
  usdTo?: number | null;
  /** What the ledger says was spent today in this project, in dollars; `null` while unknown. */
  spentTodayUsd?: number | null;
}) {
  const { flow, connection, arrived, now } = useLiveFlow({
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
  const format = (usd: number | null | undefined) => liveMoney(usd, currency, usdTo);
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
      {wall && (
        <div className="flex items-center justify-between gap-6">
          <h1
            data-verbatim
            data-testid="live-title"
            className="text-4xl font-semibold tracking-tight text-ink"
          >
            {slug} <span className="text-ink-subtle">·</span> {workflow}
          </h1>
          <Clock now={now} />
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        {switcher}
        <LiveIndicator connection={connection} wall={wall} />
        <span className="ml-auto flex items-center gap-2">{toolbar}</span>
      </div>
      <Summary flow={flow} spent={format(spentTodayUsd)} wall={wall} />
      <div ref={lanesRef} className="flex min-h-0 flex-1 gap-3 overflow-x-auto">
        {flow.lanes.map((lane) => (
          <Lane
            key={lane.state}
            lane={lane}
            cards={lane.cards.flatMap((id) => byId.get(id) ?? [])}
            name={workflow}
            wall={wall}
            arrived={arrived}
            format={format}
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
