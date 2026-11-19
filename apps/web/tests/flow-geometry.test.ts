import { describe, expect, it } from "vitest";
import { MAX_TOKENS, STATION_W, TOKEN_H, TOKEN_W, edgeOfMove, flowGeometry, parkTokens, pointAlong, travelPath } from "@/components/workflows/flow/geometry";

/**
 * The flow's drawing (S67-29): the nominal path left to right, what leaves it on a row below, and the
 * points a token walks along an edge. Pure, so a token can be followed here without a browser.
 */
const graph = {
  nodes: [
    { id: "triage", display: "Triage", lane: "agent" },
    { id: "plan", display: "Plan", lane: "agent" },
    { id: "approval", display: "Plan approval", kind: "wait", lane: "human" },
    { id: "implement", display: "Implement", lane: "agent" },
    { id: "done", display: "Done", terminal: true, lane: "terminal" },
    { id: "needs_human", display: "Needs a human", kind: "wait", lane: "human" },
  ],
  edges: [
    { id: "t1", from: "triage", to: "plan", kind: "nominal", actor: "triager" },
    { id: "t2", from: "plan", to: "approval", kind: "nominal", actor: "planner" },
    { id: "t3", from: "approval", to: "implement", kind: "nominal", actor: "a maintainer" },
    { id: "t4", from: "implement", to: "done", kind: "nominal", actor: "developer" },
    { id: "esc", from: "implement", to: "needs_human", kind: "escalate", label: "when retries run out" },
    { id: "back", from: "implement", to: "plan", kind: "reject", label: "if rejected" },
  ],
};

describe("flowGeometry", () => {
  const g = flowGeometry(graph, "triage", new Map());
  const at = (id: string) => g.byId.get(id)!;

  it("lays the nominal path left to right and the side states on the row below", () => {
    const path = ["triage", "plan", "approval", "implement", "done"].map(at);
    expect(path.map((s) => s.row)).toEqual(Array(5).fill("path"));
    for (let i = 1; i < path.length; i += 1) expect(path[i]!.x).toBeGreaterThan(path[i - 1]!.x + STATION_W - 1);
    expect(new Set(path.map((s) => s.y)).size).toBe(1);
    const side = at("needs_human");
    expect(side.row).toBe("side");
    expect(side.y).toBeGreaterThan(at("triage").y + at("triage").h);
  });

  it("starts and ends every edge on the borders of its two stations", () => {
    for (const edge of g.edges) {
      const a = at(edge.from);
      const b = at(edge.to);
      const first = edge.points[0]!;
      const last = edge.points[edge.points.length - 1]!;
      const onBorder = (p: { x: number; y: number }, s: typeof a) =>
        (Math.abs(p.x - s.x) < 1 || Math.abs(p.x - (s.x + s.w)) < 1 || Math.abs(p.y - s.y) < 1 || Math.abs(p.y - (s.y + s.h)) < 1) &&
        p.x >= s.x - 1 && p.x <= s.x + s.w + 1 && p.y >= s.y - 1 && p.y <= s.y + s.h + 1;
      expect(onBorder(first, a), edge.id).toBe(true);
      expect(onBorder(last, b), edge.id).toBe(true);
    }
  });

  it("draws a rejection as an arc back over the row, and names the side edges", () => {
    const back = g.edges.find((e) => e.id === "back")!;
    expect(back.back).toBe(true);
    expect(back.points.some((p) => p.y < at("plan").y)).toBe(true);
    expect(g.edges.find((e) => e.id === "esc")!.label).toBe("when retries run out");
    expect(g.edges.find((e) => e.id === "t1")!.label).toBe("");
  });

  it("gives a state the workflow does not define a station of its own", () => {
    const outside = flowGeometry(graph, "triage", new Map(), [{ id: "ghost", display: "Ghost" }]);
    expect(outside.byId.get("ghost")).toMatchObject({ row: "side", outside: true });
  });

  it("finds the edge of a move by its id, else by its two states", () => {
    expect(edgeOfMove(g, "triage", "plan", "t1")?.id).toBe("t1");
    expect(edgeOfMove(g, "implement", "needs_human", null)?.id).toBe("esc");
    expect(edgeOfMove(g, "done", "triage", null)).toBeUndefined();
  });
});

describe("parkTokens", () => {
  const g = flowGeometry(graph, "triage", new Map());
  it("rests each token inside its station, in the order of the lane, and counts the ones it cannot show", () => {
    const ids = Array.from({ length: MAX_TOKENS + 3 }, (_, i) => `i${i}`);
    const { parked, overflow } = parkTokens(g, [
      { state: "plan", cards: ["a", "b"] },
      { state: "done", cards: ids },
    ]);
    const plan = g.byId.get("plan")!;
    for (const id of ["a", "b"]) {
      const p = parked.get(id)!;
      expect(p.station).toBe("plan");
      expect(p.x).toBeGreaterThanOrEqual(plan.x);
      expect(p.x + TOKEN_W).toBeLessThanOrEqual(plan.x + plan.w);
      expect(p.y + TOKEN_H).toBeLessThanOrEqual(plan.y + plan.h);
    }
    expect(parked.get("a")!.x).toBeLessThan(parked.get("b")!.x);
    expect([...parked.keys()].filter((k) => k.startsWith("i"))).toHaveLength(MAX_TOKENS - 1);
    expect(overflow.get("done")).toBe(4);
  });

  it("keeps tokens inside a narrow station, and fills extra height with room", () => {
    const narrow = flowGeometry(graph, "triage", new Map(), [], { stationW: 176 });
    const { parked } = parkTokens(narrow, [{ state: "plan", cards: ["a", "b"] }]);
    const plan = narrow.byId.get("plan")!;
    expect(parked.get("b")!.x + narrow.token.w).toBeLessThanOrEqual(plan.x + plan.w);
    const tall = flowGeometry(graph, "triage", new Map(), [], { stretch: 200 });
    expect(tall.height).toBeGreaterThan(flowGeometry(graph, "triage", new Map()).height + 150);
  });
});

describe("a token's way", () => {
  const g = flowGeometry(graph, "triage", new Map());
  const edge = g.edges.find((e) => e.id === "t1");
  it("starts where the token was, passes along the edge, and ends where it rests", () => {
    const from = { x: 10, y: 80 };
    const to = { x: 400, y: 90 };
    const path = travelPath(from, to, edge);
    expect(path[0]).toEqual(from);
    expect(path[path.length - 1]).toEqual(to);
    expect(path.length).toBeGreaterThan(5);
    expect(pointAlong(path, 0)).toEqual(from);
    expect(pointAlong(path, 1)).toEqual(to);
    const mid = pointAlong(path, 0.5);
    expect(mid.x).toBeGreaterThan(from.x);
    expect(mid.x).toBeLessThan(to.x);
  });
  it("is monotone along a straight path", () => {
    const path = [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 30, y: 0 }];
    expect(pointAlong(path, 0.5).x).toBeCloseTo(15);
    expect(pointAlong(path, 2).x).toBe(30);
  });
});
