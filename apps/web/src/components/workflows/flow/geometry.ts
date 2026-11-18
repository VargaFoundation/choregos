// SPDX-License-Identifier: Apache-2.0
/**
 * A workflow's flow, laid out left to right (ADR 0187 §4, S67-29). The nominal path is a row of
 * stations; what leaves it (rejections, retries, escalations, human waits) is a second row below; the
 * transitions are smooth curves between them. It reuses the map's own reading (`layoutMap`: which
 * state is on the path, which beside it, where each side state falls in from), so the flow and the
 * map never disagree about a workflow's shape.
 *
 * Pure: the same graph and the same counts give the same drawing, and every edge carries the points
 * a token walks along, so a test can follow a token without a browser.
 */
import {
  actorKind,
  howReached,
  isNominal,
  layoutMap,
  secondaryLabel,
  type ActorKind,
  type Graph,
  type GraphEdge,
  type GraphNode,
} from "../map/layout";

export const STATION_W = 214;
export const GAP_X = 58;
export const HEAD_H = 64;
export const TOKEN_W = 96;
export const TOKEN_H = 26;
export const TOKEN_GAP = 6;
export const PAD = 8;
export const COLS = 2;
/** Tokens a station shows; beyond this the last place says how many more. */
export const MAX_TOKENS = 6;
const MARGIN_X = 20;
const SIDE_GAP = 120;
const SIDE_SPACING = 28;
const SAMPLES = 28;

export interface Pt {
  x: number;
  y: number;
}

export interface Station {
  id: string;
  display: string;
  kind: ActorKind;
  row: "path" | "side";
  terminal: boolean;
  /** A state the workflow does not define, where work items still sit. */
  outside: boolean;
  x: number;
  y: number;
  w: number;
  h: number;
  /** How one falls into a side state, in plain words (`on failure (up to 2 retries)`). */
  reached: string[];
}

export interface FlowEdge {
  /** The graph edge's id, or `from->to` when it has none: what the reduction's `transition.edge` says. */
  id: string;
  from: string;
  to: string;
  kind: string;
  label: string;
  /** SVG path data. */
  d: string;
  /** Points along the curve, first on the source's border, last on the target's: what a token follows. */
  points: Pt[];
  mid: Pt;
  /** An edge that goes back up the path (a rejection): drawn as an arc above the row. */
  back: boolean;
}

export interface FlowGeometry {
  stations: Station[];
  edges: FlowEdge[];
  width: number;
  height: number;
  byId: Map<string, Station>;
}

export const edgeId = (edge: GraphEdge): string => edge.id ?? `${edge.from}->${edge.to}`;

const rowsFor = (count: number) => Math.max(2, Math.ceil(Math.min(count, MAX_TOKENS) / COLS));
const heightFor = (rows: number) => HEAD_H + 8 + rows * (TOKEN_H + TOKEN_GAP) + 6;

function cubic(a: Pt, b: Pt, c: Pt, d: Pt, t: number): Pt {
  const u = 1 - t;
  return {
    x: u * u * u * a.x + 3 * u * u * t * b.x + 3 * u * t * t * c.x + t * t * t * d.x,
    y: u * u * u * a.y + 3 * u * u * t * b.y + 3 * u * t * t * c.y + t * t * t * d.y,
  };
}

function curve(p0: Pt, c1: Pt, c2: Pt, p3: Pt): { d: string; points: Pt[]; mid: Pt } {
  const points = Array.from({ length: SAMPLES + 1 }, (_, i) => cubic(p0, c1, c2, p3, i / SAMPLES));
  const f = (n: number) => Math.round(n * 10) / 10;
  return {
    d: `M ${f(p0.x)} ${f(p0.y)} C ${f(c1.x)} ${f(c1.y)} ${f(c2.x)} ${f(c2.y)} ${f(p3.x)} ${f(p3.y)}`,
    points,
    mid: cubic(p0, c1, c2, p3, 0.5),
  };
}

/** What a flow needs of the workflow: its graph, its initial state, and how many work items sit in each state. */
export function flowGeometry(
  graph: Graph,
  initial: string | null | undefined,
  counts: ReadonlyMap<string, number>,
  /** States work items sit in that the graph does not define. */
  outside: { id: string; display: string }[] = [],
): FlowGeometry {
  const nodes = graph.nodes as GraphNode[];
  const edges = (graph.edges as GraphEdge[]).filter((e) => e.kind !== "default" && !e.wildcard && e.from !== e.to);
  const layout = layoutMap(graph, initial);
  const node = new Map(nodes.map((n) => [n.id, n]));
  const sideIds = [...layout.sides.map((s) => s.id), ...outside.filter((o) => !node.has(o.id)).map((o) => o.id)];

  const rowRows = (ids: string[]) => ids.reduce((m, id) => Math.max(m, rowsFor(counts.get(id) ?? 0)), 2);
  const pathH = heightFor(rowRows(layout.path));
  const sideH = heightFor(rowRows(sideIds));
  const back = edges.some((e) => {
    const i = layout.path.indexOf(e.from);
    const j = layout.path.indexOf(e.to);
    return i >= 0 && j >= 0 && j < i;
  });
  const top = back ? 78 : 24;
  const pathY = top;
  const sideY = pathY + pathH + SIDE_GAP;

  const stations: Station[] = [];
  layout.path.forEach((id, i) => {
    const n = node.get(id);
    stations.push({
      id,
      display: n?.display ?? id,
      kind: n?.terminal ? "end" : actorKind(n?.lane),
      row: "path",
      terminal: Boolean(n?.terminal),
      outside: false,
      x: MARGIN_X + i * (STATION_W + GAP_X),
      y: pathY,
      w: STATION_W,
      h: pathH,
      reached: [],
    });
  });
  const byId = new Map(stations.map((s) => [s.id, s]));
  let cursor = MARGIN_X;
  for (const id of sideIds) {
    const n = node.get(id);
    const side = layout.sides.find((s) => s.id === id);
    const anchor = side?.reachedFrom.map((d) => byId.get(d.id)).find((s) => s?.row === "path");
    const x = Math.max(cursor, anchor ? anchor.x + 24 : cursor);
    cursor = x + STATION_W + SIDE_SPACING;
    const extra = outside.find((o) => o.id === id);
    const station: Station = {
      id,
      display: n?.display ?? extra?.display ?? id,
      kind: n?.terminal ? "end" : n ? actorKind(n.lane) : "platform",
      row: "side",
      terminal: Boolean(n?.terminal),
      outside: !n,
      x,
      y: sideY,
      w: STATION_W,
      h: sideH,
      reached: (side?.reachedFrom ?? []).map((d) => d.how),
    };
    stations.push(station);
    byId.set(id, station);
  }

  const width = Math.max(...stations.map((s) => s.x + s.w), 0) + MARGIN_X;
  const bottom = stations.some((s) => s.row === "side") ? sideY + sideH : pathY + pathH;
  const height = bottom + 28;

  const flowEdges: FlowEdge[] = [];
  const used = new Map<string, number>();
  for (const edge of edges) {
    const a = byId.get(edge.from);
    const b = byId.get(edge.to);
    if (!a || !b) continue;
    const pair = `${a.id}|${b.id}`;
    const k = used.get(pair) ?? 0;
    used.set(pair, k + 1);
    const shift = k * 16;
    let shape: { d: string; points: Pt[]; mid: Pt };
    let isBack = false;
    if (a.row === "path" && b.row === "path") {
      const i = layout.path.indexOf(a.id);
      const j = layout.path.indexOf(b.id);
      if (j === i + 1) {
        const p0 = { x: a.x + a.w, y: a.y + 30 + shift };
        const p3 = { x: b.x, y: b.y + 30 + shift };
        const dx = (p3.x - p0.x) * 0.5;
        shape = curve(p0, { x: p0.x + dx, y: p0.y }, { x: p3.x - dx, y: p3.y }, p3);
      } else {
        // A jump forward or a step back: an arc above the row.
        isBack = j < i;
        const lift = 34 + 10 * Math.abs(j - i);
        const p0 = { x: a.x + a.w / 2 + (isBack ? -24 : 24) + shift, y: a.y };
        const p3 = { x: b.x + b.w / 2 + (isBack ? 24 : -24) + shift, y: b.y };
        shape = curve(p0, { x: p0.x, y: p0.y - lift }, { x: p3.x, y: p3.y - lift }, p3);
      }
    } else if (a.row === "path" && b.row === "side") {
      const p0 = { x: a.x + a.w * 0.35 + shift, y: a.y + a.h };
      const p3 = { x: b.x + b.w * 0.35 + shift, y: b.y };
      const dy = (p3.y - p0.y) * 0.5;
      shape = curve(p0, { x: p0.x, y: p0.y + dy }, { x: p3.x, y: p3.y - dy }, p3);
    } else if (a.row === "side" && b.row === "path") {
      const p0 = { x: a.x + a.w * 0.65 + shift, y: a.y };
      const p3 = { x: b.x + b.w * 0.65 + shift, y: b.y + b.h };
      const dy = (p0.y - p3.y) * 0.5;
      shape = curve(p0, { x: p0.x, y: p0.y - dy }, { x: p3.x, y: p3.y + dy }, p3);
    } else {
      const forward = b.x >= a.x;
      const p0 = { x: forward ? a.x + a.w : a.x, y: a.y + 30 + shift };
      const p3 = { x: forward ? b.x : b.x + b.w, y: b.y + 30 + shift };
      const dx = (p3.x - p0.x) * 0.5;
      shape = curve(p0, { x: p0.x + dx, y: p0.y }, { x: p3.x - dx, y: p3.y }, p3);
    }
    flowEdges.push({
      id: edgeId(edge),
      from: edge.from,
      to: edge.to,
      kind: edge.kind ?? "nominal",
      label: isNominal(edge) ? "" : secondaryLabel(edge) || howReached(edge),
      ...shape,
      back: isBack,
    });
  }
  return { stations, edges: flowEdges, width, height, byId };
}

/** The edge a move took: by the reduction's edge id, else the first edge between the two states. */
export function edgeOfMove(
  geometry: FlowGeometry,
  from: string,
  to: string,
  edge: string | null | undefined,
): FlowEdge | undefined {
  return (
    (edge ? geometry.edges.find((e) => e.id === edge) : undefined) ??
    geometry.edges.find((e) => e.from === from && e.to === to)
  );
}

export interface Parked {
  x: number;
  y: number;
  station: string;
}

/** Where each work item's token rests: the stations' places in order, and how many a station could not show. */
export function parkTokens(
  geometry: FlowGeometry,
  lanes: { state: string; cards: string[] }[],
): { parked: Map<string, Parked>; overflow: Map<string, number> } {
  const parked = new Map<string, Parked>();
  const overflow = new Map<string, number>();
  for (const lane of lanes) {
    const station = geometry.byId.get(lane.state);
    if (!station) continue;
    const many = lane.cards.length > MAX_TOKENS;
    const shown = many ? MAX_TOKENS - 1 : lane.cards.length;
    lane.cards.slice(0, shown).forEach((id, i) => {
      parked.set(id, {
        station: station.id,
        x: station.x + PAD + (i % COLS) * (TOKEN_W + TOKEN_GAP),
        y: station.y + HEAD_H + 6 + Math.floor(i / COLS) * (TOKEN_H + TOKEN_GAP),
      });
    });
    if (many) overflow.set(station.id, lane.cards.length - shown);
  }
  return { parked, overflow };
}

/**
 * The positions a token passes through from where it was to where it rests: the old place, then the
 * edge's points, then the new place, as top-left corners (the edge's points are where the token's
 * centre goes). Reaching each point at a time eased over the whole distance is the caller's.
 */
export function travelPath(from: Pt, to: Pt, edge: FlowEdge | undefined): Pt[] {
  const half = { x: TOKEN_W / 2, y: TOKEN_H / 2 };
  const along = (edge?.points ?? []).map((p) => ({ x: p.x - half.x, y: p.y - half.y }));
  return [from, ...along, to];
}

/** A point at `t` (0..1) of the way along a polyline, by length. */
export function pointAlong(path: Pt[], t: number): Pt {
  if (path.length === 0) return { x: 0, y: 0 };
  const lengths: number[] = [0];
  for (let i = 1; i < path.length; i += 1) {
    const a = path[i - 1]!;
    const b = path[i]!;
    lengths.push(lengths[i - 1]! + Math.hypot(b.x - a.x, b.y - a.y));
  }
  const total = lengths[lengths.length - 1]!;
  if (total === 0) return path[path.length - 1]!;
  const want = Math.min(1, Math.max(0, t)) * total;
  for (let i = 1; i < path.length; i += 1) {
    if (lengths[i]! >= want) {
      const span = lengths[i]! - lengths[i - 1]!;
      const u = span === 0 ? 0 : (want - lengths[i - 1]!) / span;
      const a = path[i - 1]!;
      const b = path[i]!;
      return { x: a.x + (b.x - a.x) * u, y: a.y + (b.y - a.y) * u };
    }
  }
  return path[path.length - 1]!;
}
