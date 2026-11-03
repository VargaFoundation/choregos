// SPDX-License-Identifier: Apache-2.0
/**
 * Where each box of the journey sits (S22-02): the path as a snake (one row from left to right, the
 * next from right to left, joined by a U-turn), the detours under the row of the step that sends
 * there, the loops back as arcs above. The number of columns follows the width: the map never
 * overflows, it grows longer.
 *
 * Pure functions: the same width gives the same coordinates.
 */
import type { JourneyModel } from "./model";

export type Point = { x: number; y: number };
export type Segment = {
  /** The state the work item occupies on this stretch: between the box that leads there and the one that leaves it. */
  state: string;
  d: string;
  label: Point & { anchor: "start" | "middle" | "end"; maxWidth: number };
};
export type Placement = {
  width: number;
  height: number;
  columns: number;
  start: Point;
  end: Point;
  /** The steps' boxes, path and detours. */
  steps: Map<string, Point>;
  segments: Segment[];
  arcs: Map<string, { d: string; label: Point }>;
};

export const RADIUS = 13;
const TOP = 92;
/** Under a box: its title, its action, its detail, on two lines at most each. */
const BELOW_BOX = 104;
/** The workflow map adds one line under the action — what the step produces — of two lines at most. */
const OUTPUTS_LINE = 34;
/** Above a row: the room for the loop-back arcs. */
const ABOVE = 76;
const DETOUR_DROP = 150;

export function place(model: JourneyModel, width: number, withOutputs = false): Placement {
  const belowBox = BELOW_BOX + (withOutputs ? OUTPUTS_LINE : 0);
  // On a phone, tighter margins and pitch: two columns fit in 340 px.
  const narrow = width < 600;
  const MARGIN_X = narrow ? 40 : 60;
  const MIN_PITCH = narrow ? 118 : 128;
  const items = ["start", ...model.steps.map((e) => e.id), "end"];
  const usable = Math.max(width, 2 * MIN_PITCH + 2 * MARGIN_X) - 2 * MARGIN_X;
  const columns = Math.max(2, Math.min(items.length, Math.floor(usable / MIN_PITCH)));
  const pitch = usable / columns;
  const rowOf = (k: number) => Math.floor(k / columns);
  const rowCount = rowOf(items.length - 1) + 1;

  // A row a detour leaves from is taller: the detour hangs below it, with its labels.
  const rowsWithDetour = new Set<number>();
  for (const detour of model.detours) {
    const k = items.indexOf(detour.sources[0] ?? "");
    if (k >= 0) rowsWithDetour.add(rowOf(k));
  }
  const heightOf = (r: number) => belowBox + ABOVE + (rowsWithDetour.has(r) ? DETOUR_DROP : 0);
  const ys: number[] = [TOP];
  for (let r = 1; r < rowCount; r += 1) ys.push(ys[r - 1]! + heightOf(r - 1));

  const position = (k: number): Point & { row: number } => {
    const r = rowOf(k);
    const c = k % columns;
    const col = r % 2 === 0 ? c : columns - 1 - c;
    return { x: MARGIN_X + (col + 0.5) * pitch, y: ys[r]!, row: r };
  };
  const positions = items.map((_, k) => position(k));
  const steps = new Map<string, Point>();
  model.steps.forEach((e, i) => steps.set(e.id, positions[i + 1]!));

  // The stretches: the state between two consecutive boxes. A change of row is a U-turn.
  const segments: Segment[] = [];
  const midpoints = new Map<string, Point>();
  for (let k = 0; k + 1 < items.length; k += 1) {
    const a = positions[k]!;
    const b = positions[k + 1]!;
    const state = model.path[k] ?? "";
    if (a.row === b.row) {
      const direction = Math.sign(b.x - a.x) || 1;
      midpoints.set(state, { x: (a.x + b.x) / 2, y: a.y });
      segments.push({
        state,
        d: `M ${a.x + direction * RADIUS} ${a.y} L ${b.x - direction * RADIUS} ${b.y}`,
        label: { x: (a.x + b.x) / 2, y: a.y - 10, anchor: "middle", maxWidth: pitch - 2 * RADIUS - 12 },
      });
    } else {
      // Even row: turn right; odd row: turn left.
      const s = (a.row % 2 === 0 ? 1 : -1) * (MARGIN_X - 10) * 1.3;
      midpoints.set(state, { x: a.x + s * 0.75, y: (a.y + b.y) / 2 });
      segments.push({
        state,
        d: `M ${a.x} ${a.y + RADIUS} C ${a.x + s} ${a.y + RADIUS}, ${b.x + s} ${b.y - RADIUS}, ${b.x} ${b.y - RADIUS}`,
        // Just above the next row: the middle of the U-turn is where the detours go.
        label: {
          x: b.x + s * 0.62 - Math.sign(s) * 6,
          y: b.y - 34,
          anchor: s > 0 ? "end" : "start",
          maxWidth: pitch * 0.9,
        },
      });
    }
  }

  // The detours: under the step that sends there first, lower than its labels; two neighbours push each other.
  const placed: Point[] = [];
  for (const detour of model.detours) {
    const sources = detour.sources.map((id) => steps.get(id)).filter((p): p is Point => Boolean(p));
    const first = sources[0];
    if (!first) continue;
    const sameRow = sources.filter((p) => p.y === first.y);
    let x = sameRow.reduce((sum, p) => sum + p.x, 0) / sameRow.length;
    const y = first.y + DETOUR_DROP;
    while (placed.some((p) => p.y === y && Math.abs(p.x - x) < pitch * 0.9)) x += pitch;
    x = Math.min(Math.max(x, MARGIN_X + pitch / 2), MARGIN_X + usable - pitch / 2);
    const point = { x, y };
    placed.push(point);
    steps.set(detour.step.id, point);
  }

  // The arcs pass UNDER the labels (which have a background): they are drawn the shortest way, without going around them.
  const arcs = new Map<string, { d: string; label: Point }>();
  for (const arc of model.arcs) {
    const origin = steps.get(arc.fromStep);
    if (!origin) continue;
    if (arc.kind === "back") {
      // Towards the stretch of the state one returns to, from below.
      const target = "state" in arc.target ? midpoints.get(arc.target.state) : undefined;
      if (!target) continue;
      const side = target.x >= origin.x ? 1 : -1;
      arcs.set(arc.key, {
        d: `M ${origin.x + side * RADIUS} ${origin.y} C ${origin.x + side * 80} ${origin.y}, ${target.x} ${target.y + 110}, ${target.x} ${target.y + 3}`,
        label: { x: (origin.x + target.x) / 2, y: (origin.y + target.y) / 2 },
      });
      continue;
    }
    const targetId =
      "step" in arc.target
        ? arc.target.step
        : "state" in arc.target
          ? model.steps.find((e) => e.from === (arc.target as { state: string }).state)?.id
          : undefined;
    const arrival = targetId ? steps.get(targetId) : undefined;
    if (!arrival) continue;
    if (arc.kind === "loop" && origin.y === arrival.y) {
      // Above the row, higher the more boxes the loop skips.
      const h = 40 + Math.min(Math.abs(origin.x - arrival.x) / pitch, 5) * 6;
      arcs.set(arc.key, {
        d: `M ${origin.x} ${origin.y - RADIUS} C ${origin.x} ${origin.y - RADIUS - h}, ${arrival.x} ${arrival.y - RADIUS - h}, ${arrival.x} ${arrival.y - RADIUS}`,
        label: { x: (origin.x + arrival.x) / 2, y: origin.y - RADIUS - h * 0.75 - 6 },
      });
    } else if (arc.kind === "loop") {
      arcs.set(arc.key, {
        d: `M ${origin.x} ${origin.y - RADIUS} C ${origin.x} ${origin.y - 90}, ${arrival.x} ${arrival.y + 120}, ${arrival.x} ${arrival.y + RADIUS}`,
        label: { x: (origin.x + arrival.x) / 2, y: (origin.y + arrival.y) / 2 },
      });
    } else {
      // A detour's entry: from the bottom of the step that sends back to the top of the detour's box.
      arcs.set(arc.key, {
        d: `M ${origin.x} ${origin.y + RADIUS} C ${origin.x} ${origin.y + 110}, ${arrival.x} ${arrival.y - 70}, ${arrival.x} ${arrival.y - RADIUS - 2}`,
        label: { x: (origin.x + arrival.x) / 2, y: (origin.y + arrival.y) / 2 + 4 },
      });
    }
  }

  const last = positions.at(-1)!;
  const boxesBottom = Math.max(last.y, ...positions.map((p) => p.y), ...placed.map((p) => p.y));
  return {
    width: MARGIN_X * 2 + usable,
    height: boxesBottom + belowBox,
    columns,
    start: positions[0]!,
    end: last,
    steps,
    segments,
    arcs,
  };
}

/** Truncates a label to the room it is given (an estimate: 6.4 px per character at 11 px). */
export function truncate(text: string, maxWidth: number, size = 11): string {
  const max = Math.max(4, Math.floor(maxWidth / (size * 0.58)));
  return text.length <= max ? text : `${text.slice(0, max - 1).trimEnd()}…`;
}
