// SPDX-License-Identifier: Apache-2.0
"use client";

import { Bot, CircleUser, Cog, Flag, Hourglass, TrainFront, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { stageLine, useReducedMotion } from "@/components/live/live-kit";
import { cn } from "@/lib/cn";
import { formatElapsed } from "@/lib/activity";
import type { LiveCard, LiveView } from "@/lib/live-flow";
import { workflowHue, type Hue } from "@/lib/workflow-hue";
import { KIND_COLOURS, KIND_LABEL, type ActorKind, type Graph, type GraphEdge } from "../map/layout";
import {
  HEAD_H,
  MAX_TOKENS,
  MIN_STATION_W,
  PAD,
  STATION_W,
  TOKEN_GAP,
  edgeOfMove,
  flowGeometry,
  parkTokens,
  pointAlong,
  travelPath,
  type FlowEdge,
  type FlowGeometry,
  type Parked,
  type Station,
} from "./geometry";

/** How long a token takes along an edge, and how long the edge stays lit after it. */
export const TRAVEL_MS = 1100;
export const LIT_MS = 2600;

const KIND_ICON: Record<ActorKind, LucideIcon> = {
  agent: Bot,
  human: CircleUser,
  platform: Cog,
  train: TrainFront,
  decision: Flag,
  waiting: Hourglass,
  end: Flag,
};

/** The workflow's colour as a variable, written out in full: the provenance test reads the tokens as text. */
const HUE_VAR: Record<Hue, string> = {
  1: "var(--choregos-cat-1-solid)",
  2: "var(--choregos-cat-2-solid)",
  3: "var(--choregos-cat-3-solid)",
  4: "var(--choregos-cat-4-solid)",
  5: "var(--choregos-cat-5-solid)",
  6: "var(--choregos-cat-6-solid)",
  7: "var(--choregos-cat-7-solid)",
  8: "var(--choregos-cat-8-solid)",
};

const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/** How large the wall's text is meant to be, against the page's: the flow is read from across a room. */
export const WALL_TEXT = 1.6;
const WALL_MAX = 2.2;

/** The size of a box, kept up to date. */
function useBox(box: React.RefObject<HTMLDivElement | null>) {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const read = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    read();
    const observer = new ResizeObserver(read);
    observer.observe(el);
    return () => observer.disconnect();
  }, [box]);
  return size;
}

/**
 * The drawing and the scale it is shown at. On a page the scale is 1 (the text never shrinks; a wide
 * flow scrolls). On the wall the scale is as large as the screen allows, down to the stations getting
 * narrower rather than the text smaller, and the rows are stretched to fill the height.
 */
export function layoutFor(
  wall: boolean,
  size: { w: number; h: number } | null,
  build: (options: { stationW?: number; gapX?: number; stretch?: number }) => FlowGeometry,
): { geometry: FlowGeometry; scale: number } {
  const natural = build({});
  if (!wall || !size || size.w === 0 || size.h === 0) return { geometry: natural, scale: 1 };
  let geometry = natural;
  let scale = Math.min(WALL_MAX, size.w / geometry.width);
  for (let w = STATION_W; scale < WALL_TEXT && w > MIN_STATION_W; ) {
    w = Math.max(MIN_STATION_W, w - 8);
    geometry = build({ stationW: w, gapX: Math.max(36, Math.round(w * 0.25)) });
    scale = Math.min(WALL_MAX, size.w / geometry.width);
  }
  scale = Math.min(scale, size.h / geometry.height);
  const gapOf = (g: FlowGeometry) => (g.stations[1] && g.stations[0] ? g.stations[1].x - g.stations[0].x - g.stations[0].w : undefined);
  const stretch = Math.max(0, size.h / scale - geometry.height);
  if (stretch > 0) geometry = build({ stationW: geometry.stations[0]?.w, gapX: gapOf(geometry), stretch });
  return { geometry, scale };
}

function Token({
  card,
  at,
  size,
  hue,
  now,
  slug,
  format,
  arrived,
}: {
  card: LiveCard;
  at: Parked;
  size: { w: number; h: number };
  hue: string;
  now: number;
  slug: string;
  format: (usd: number | null | undefined) => string;
  arrived: boolean;
}) {
  const run = card.running;
  const elapsed = run?.started_at ? Math.max(0, now - Date.parse(run.started_at)) : (run?.elapsed_ms ?? 0);
  const tone =
    card.status === "failed"
      ? "border-failed-line bg-failed-soft text-failed-ink"
      : card.status === "waiting"
        ? "border-waiting-line bg-waiting-soft text-waiting-ink"
        : card.status === "running"
          ? "border-transparent bg-surface text-ink"
          : "border-line-strong bg-surface text-ink";
  const detail =
    card.status === "running" && run
      ? `${stageLine(run.stage, run.agent)} · ${formatElapsed(elapsed)}${run.cost_usd != null ? ` · ${format(run.cost_usd)}` : ""}`
      : card.status === "failed"
        ? `Failed: ${(card.failure?.reason ?? "").replace(/_/g, " ")}`
        : card.status === "waiting"
          ? `Waiting: ${card.waiting?.label ?? ""}`
          : "";
  const id = `flow-tip-${card.id}`;
  return (
    <div
      data-token-id={card.id}
      data-station={at.station}
      data-status={card.status}
      data-arrived={arrived ? "true" : undefined}
      className="flow-token group absolute z-10 transition-[left,top] duration-500 ease-out"
      style={{ left: at.x, top: at.y, width: size.w, height: size.h, ["--live-hue" as string]: hue }}
    >
      <div className="flow-token-body relative size-full">
        {card.status === "running" && <span aria-hidden className="flow-ring" />}
        <Link
          href={`/p/${slug}/items/${card.id}`}
          aria-describedby={id}
          aria-label={`${card.key}, ${card.title}${detail ? `, ${detail}` : ""}`}
          data-testid="flow-token"
          className={cn(
            "relative flex size-full items-center gap-1.5 rounded-pill border px-2.5 font-mono text-xs font-medium no-underline shadow-sm",
            tone,
            card.status === "failed" && "flow-flash",
            arrived && "live-arrived",
          )}
        >
          <span
            aria-hidden
            className={cn(
              "size-2 shrink-0 rounded-full",
              card.status === "failed"
                ? "bg-failed"
                : card.status === "waiting"
                  ? "bg-waiting"
                  : card.status === "running"
                    ? "bg-running"
                    : "bg-neutral",
            )}
          />
          <span className="truncate">{card.key}</span>
        </Link>
        <span
          role="tooltip"
          id={id}
          className={cn(
            "pointer-events-none absolute bottom-full left-0 z-30 mb-2 hidden w-64 rounded-md border border-line bg-surface p-2.5 font-sans text-xs leading-snug text-ink shadow-lg",
            "group-focus-within:block group-hover:block",
          )}
        >
          <span className="block font-medium">{card.title}</span>
          {detail && <span className="mt-1 block tabular-nums text-ink-muted">{detail}</span>}
          {card.status === "failed" && card.failure?.summary && (
            <span className="mt-1 block text-ink-muted">{card.failure.summary}</span>
          )}
        </span>
      </div>
    </div>
  );
}

function Stage({
  station,
  count,
  actor,
  running,
  selected,
  hue,
  overflow,
  token,
  cols,
  onSelect,
  closedToday,
}: {
  station: Station;
  count: number;
  actor: string | null;
  running: boolean;
  selected: boolean;
  hue: string;
  overflow: number;
  token: { w: number; h: number };
  cols: number;
  onSelect: () => void;
  closedToday: number;
}) {
  const colours = KIND_COLOURS[station.kind];
  const Icon = KIND_ICON[station.kind];
  return (
    <>
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        aria-label={`${station.display}, ${KIND_LABEL[station.kind]}, ${count} ${count === 1 ? "item" : "items"}`}
        data-testid="flow-station"
        data-station-id={station.id}
        data-running={running ? "true" : undefined}
        data-selected={selected ? "true" : undefined}
        className={cn(
          "flow-station absolute flex flex-col items-stretch justify-start rounded-xl border bg-surface text-left",
          station.row === "side" && "flow-station-side",
          running && "live-running",
          selected ? "border-line-strong ring-2 ring-focus" : "border-line",
        )}
        style={{
          left: station.x,
          top: station.y,
          width: station.w,
          height: station.h,
          ["--live-hue" as string]: hue,
          borderTop: `3px solid ${colours.border}`,
        }}
      >
        <span className="flex flex-col gap-1 px-3 pt-3">
          <span className="block truncate text-base font-semibold leading-tight">{station.display}</span>
          <span className="flex items-center gap-1.5 text-xs leading-tight">
            <span
              data-testid="flow-actor"
              className="flex min-w-0 flex-1 items-center gap-1.5"
              style={{ color: colours.text }}
            >
              <Icon aria-hidden className="size-3.5 shrink-0" />
              <span className="truncate">{actor ?? KIND_LABEL[station.kind]}</span>
            </span>
            {station.terminal && closedToday > 0 && (
              <span className="shrink-0 text-succeeded-ink">{closedToday} today</span>
            )}
            <span
              data-testid="flow-count"
              className="shrink-0 rounded-pill bg-surface-sunken px-1.5 py-px font-medium tabular-nums text-ink-muted"
            >
              {count}
            </span>
          </span>
        </span>
        {station.reached.length > 0 && count === 0 && (
          <span className="absolute inset-x-3 bottom-2 truncate text-xs text-ink-subtle">{station.reached[0]}</span>
        )}
      </button>
      {overflow > 0 && (
        <span
          className="absolute z-10 flex items-center justify-center rounded-pill border border-line bg-surface-sunken text-xs font-medium text-ink-muted"
          style={{
            left: station.x + PAD + ((MAX_TOKENS - 1) % cols) * (token.w + TOKEN_GAP),
            top: station.y + HEAD_H + 6 + Math.floor((MAX_TOKENS - 1) / cols) * (token.h + TOKEN_GAP),
            width: token.w,
            height: token.h,
          }}
        >
          +{overflow} more
        </span>
      )}
    </>
  );
}

/**
 * A workflow's flow (ADR 0187 §4, S67-29): its stations left to right, the side states below, and a
 * token per work item resting where its work item is. When a work item changes state its token walks
 * along the edge between the two stations (the Web Animations API, no library) and the edge lights up;
 * with reduced motion it simply is somewhere else.
 */
export function FlowView({
  graph,
  initial,
  flow,
  workflow,
  slug,
  now,
  wall,
  arrived,
  selected,
  onSelect,
  format,
}: {
  graph: Graph;
  initial?: string | null;
  flow: LiveView;
  workflow: string;
  slug: string;
  now: number;
  wall: boolean;
  arrived: ReadonlySet<string>;
  selected: string | null;
  onSelect: (station: string | null) => void;
  format: (usd: number | null | undefined) => string;
}) {
  const hue = HUE_VAR[workflowHue(workflow)];
  const reduced = useReducedMotion();
  const box = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);

  // Keys keep the geometry from being recomputed every second: only counts and places matter to it.
  const lanesKey = JSON.stringify(flow.lanes.map((l) => [l.state, l.display, l.count, l.hors_workflow, l.cards]));
  const size = useBox(box);
  const { geometry, scale, parked, overflow } = useMemo(() => {
    const lanes = (JSON.parse(lanesKey) as [string, string, number, boolean, string[]][]).map(
      ([state, display, count, outside, cards]) => ({ state, display, count, outside, cards }),
    );
    const counts = new Map(lanes.map((l) => [l.state, l.count]));
    const outside = lanes.filter((l) => l.outside && l.count > 0).map((l) => ({ id: l.state, display: l.display }));
    const { geometry, scale } = layoutFor(wall, size, (o) => flowGeometry(graph, initial, counts, outside, o));
    return { geometry, scale, ...parkTokens(geometry, lanes) };
  }, [graph, initial, lanesKey, wall, size]);
  const cards = useMemo(() => new Map(flow.cards.map((c) => [c.id, c])), [flow.cards]);
  const edges = geometry.edges;
  const actorOf = useMemo(() => {
    const out = new Map<string, string>();
    for (const edge of graph.edges as GraphEdge[]) {
      if ((!edge.kind || edge.kind === "nominal") && edge.actor && !out.has(edge.from)) out.set(edge.from, edge.actor);
    }
    return out;
  }, [graph]);

  // Tokens that changed station since the last drawing walk to their new place.
  const before = useRef(new Map<string, Parked>());
  const lit = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  useLayoutEffect(() => {
    const root = stage.current;
    const previous = before.current;
    before.current = parked;
    if (!root) return;
    for (const [id, now] of parked) {
      const was = previous.get(id);
      const card = cards.get(id);
      if (!was || !card || was.station === now.station || !card.transition) continue;
      const t = card.transition;
      const exact = t.from === was.station && t.to === now.station ? t.edge : null;
      const edge = edgeOfMove(geometry, was.station, now.station, exact);
      if (edge) {
        const g = root.querySelector<SVGGElement>(`[data-edge-id="${CSS.escape(edge.id)}"]`);
        if (g) {
          g.dataset.lit = "true";
          clearTimeout(lit.current.get(edge.id));
          lit.current.set(
            edge.id,
            setTimeout(() => {
              delete g.dataset.lit;
            }, LIT_MS),
          );
        }
      }
      const el = root.querySelector<HTMLElement>(`[data-token-id="${CSS.escape(id)}"]`);
      const body = el?.querySelector<HTMLElement>(".flow-token-body");
      if (!el || !body || reduced || typeof body.animate !== "function") continue;
      // The new place is already applied; the transition on the move is switched off for this change.
      el.style.transition = "none";
      void el.offsetWidth;
      el.style.transition = "";
      const path = travelPath({ x: was.x, y: was.y }, { x: now.x, y: now.y }, edge, geometry.token);
      const frames = Array.from({ length: 41 }, (_, i) => {
        const u = i / 40;
        const p = pointAlong(path, ease(u));
        const lift = 1 + 0.1 * Math.sin(Math.PI * u);
        return { transform: `translate(${p.x - now.x}px, ${p.y - now.y}px) scale(${lift})` };
      });
      el.style.zIndex = "30";
      const animation = body.animate(frames, { duration: TRAVEL_MS, easing: "linear" });
      const done = () => {
        el.style.zIndex = "";
      };
      animation.addEventListener?.("finish", done);
      animation.addEventListener?.("cancel", done);
    }
  });
  useLayoutEffect(
    () => () => {
      for (const timer of lit.current.values()) clearTimeout(timer);
    },
    [],
  );

  // On a page the flow scrolls rather than shrinks: the station the work is at is kept in view.
  const focus = flow.cards.find((c) => c.status === "running")?.lane ?? flow.lanes.find((l) => l.count > 0)?.state;
  const focusX = focus ? geometry.byId.get(focus) : undefined;
  const focusLeft = focusX ? focusX.x + focusX.w / 2 : null;
  useLayoutEffect(() => {
    const el = box.current;
    if (wall || !el || focusLeft === null || typeof el.scrollTo !== "function") return;
    const target = Math.max(0, focusLeft * scale - el.clientWidth / 2);
    if (Math.abs(el.scrollLeft - target) < 4) return;
    el.scrollTo({ left: target, behavior: reduced ? "auto" : "smooth" });
  }, [focus, focusLeft, wall, scale, reduced]);

  const running = new Set(flow.cards.filter((c) => c.status === "running").map((c) => c.lane));
  const lanes = new Map(flow.lanes.map((l) => [l.state, l]));
  return (
    <div
      ref={box}
      data-testid="flow-view"
      className={cn("w-full", wall ? "flex h-full items-center overflow-hidden" : "overflow-x-auto")}
      style={wall ? undefined : { minHeight: geometry.height * scale }}
    >
      <div style={{ width: geometry.width * scale, height: geometry.height * scale }} className="mx-auto">
        <div
          ref={stage}
          className="relative origin-top-left"
          style={{ width: geometry.width, height: geometry.height, transform: `scale(${scale})` }}
        >
          <svg
            width={geometry.width}
            height={geometry.height}
            className="pointer-events-none absolute inset-0"
            aria-hidden
            data-testid="flow-edges"
          >
            {edges.map((edge: FlowEdge) => (
              <g
                key={`${edge.id}|${edge.from}|${edge.to}`}
                data-edge-id={edge.id}
                data-kind={edge.kind}
                className={cn("flow-edge", edge.kind !== "nominal" && "flow-edge-side")}
                style={{ ["--live-hue" as string]: hue }}
              >
                <path d={edge.d} className="flow-edge-base" fill="none" markerEnd="url(#flow-arrow)" />
                <path d={edge.d} className="flow-edge-pulse" fill="none" />
                {edge.label && (
                  <text
                    x={edge.back ? edge.mid.x : edge.mid.x + 10}
                    y={edge.back ? edge.mid.y - 8 : edge.mid.y + 4}
                    textAnchor={edge.back ? "middle" : "start"}
                    className="flow-edge-label"
                  >
                    {edge.label}
                  </text>
                )}
              </g>
            ))}
            <defs>
              <marker id="flow-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto">
                <path d="M 1 1 L 9 5 L 1 9" fill="none" stroke="context-stroke" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </marker>
            </defs>
          </svg>
          {geometry.stations.map((station) => (
            <Stage
              key={station.id}
              station={station}
              count={lanes.get(station.id)?.count ?? 0}
              closedToday={lanes.get(station.id)?.closed_today ?? 0}
              actor={actorOf.get(station.id) ?? null}
              running={running.has(station.id)}
              selected={selected === station.id}
              hue={hue}
              overflow={overflow.get(station.id) ?? 0}
              token={geometry.token}
              cols={geometry.cols}
              onSelect={() => onSelect(selected === station.id ? null : station.id)}
            />
          ))}
          {[...parked].map(([id, at]) => {
            const card = cards.get(id);
            return card ? (
              <Token
                key={id}
                card={card}
                at={at}
                size={geometry.token}
                hue={hue}
                now={now}
                slug={slug}
                format={format}
                arrived={arrived.has(id)}
              />
            ) : null;
          })}
        </div>
      </div>
    </div>
  );
}
