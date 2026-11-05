import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  mapEdge,
  nominalPath,
  KIND_COLOURS,
  defaults,
  layoutMap,
  actorKind,
  secondaryLabel,
  navigation,
} from "@/components/workflows/map/layout";
import { releaseFullValidation } from "@/mocks/data";

const n = (id: string, lane = "agent", terminal = false) => ({ id, display: id.toUpperCase(), lane, terminal });
const nominal = (id: string, from: string, to: string, extra: object = {}) => ({ id, from, to, kind: "nominal", ...extra });
const sec = (from: string, to: string, kind: string, label: string) => ({ id: `${from}->${to}:${kind}:${label}`, from, to, kind, label });

/** default-simple, reduced: a path, an escalation, a rejection, an abandon, defaults. */
const simple = {
  nodes: [n("inbox"), n("spec", "human"), n("dev"), n("pr", "system"), n("prod", "terminal", true), n("needs_human", "human"), n("refining"), n("abandoned", "terminal", true)],
  edges: [
    nominal("t-refine", "inbox", "spec", { actor: "refiner" }),
    nominal("t-approve", "spec", "dev", { actor: "owner" }),
    sec("spec", "refining", "reject", "if rejected"),
    nominal("t-again", "refining", "spec", { actor: "refiner" }),
    nominal("t-dev", "dev", "pr", { actor: "dev", gates: ["scope_respected"] }),
    sec("dev", "dev", "retry", "on failure (≤2)"),
    sec("dev", "needs_human", "escalate", "when retries run out"),
    nominal("t-pr", "pr", "prod", { actor: "ci" }),
    sec("inbox", "needs_human", "default", "question"),
    sec("dev", "needs_human", "default", "question"),
    sec("needs_human", "abandoned", "default", "abandon"),
  ],
};

describe("the flat map: the layout (S21-07)", () => {
  it("the nominal path starts from the declared initial state and follows the longest run of nominal transitions", () => {
    expect(nominalPath(simple, "inbox")).toEqual(["inbox", "spec", "dev", "pr", "prod"]);
    // A shortcut (spec → pr) does not steal the path: the longest run wins.
    const shortcut = { ...simple, edges: [...simple.edges, nominal("t-skip", "spec", "pr")] };
    expect(nominalPath(shortcut, "inbox")).toEqual(["inbox", "spec", "dev", "pr", "prod"]);
  });

  it("without a declared initial state, it starts from the state nothing reaches", () => {
    const mixed = { ...simple, nodes: [simple.nodes[3]!, ...simple.nodes.filter((x) => x.id !== "pr")] };
    expect(nominalPath(mixed)[0]).toBe("inbox");
  });

  it("a state reached only by a rejection, a retry, an escalation or a default goes in the column beside", () => {
    const { path, sides } = layoutMap(simple, "inbox");
    expect(path).not.toContain("needs_human");
    expect(sides.map((c) => c.id)).toEqual(["refining", "needs_human", "abandoned"]);
    expect(sides.find((c) => c.id === "refining")!.reachedFrom).toEqual([{ id: "spec", how: "if rejected" }]);
  });

  it("a side state lines up with the state one falls in from; the abandon after the human intervention", () => {
    const order = layoutMap(simple, "inbox").sides.map((c) => c.id);
    expect(order.indexOf("refining")).toBeLessThan(order.indexOf("needs_human"));
    expect(order.indexOf("needs_human")).toBeLessThan(order.indexOf("abandoned"));
  });

  it("a transition from any agent state is not part of the path and is edited under its own identifier", () => {
    const withWildcard = {
      ...simple,
      edges: [
        ...simple.edges,
        { ...nominal("t-ask:inbox", "inbox", "needs_human"), wildcard: true, actor: "ci" },
        { ...nominal("t-ask:dev", "dev", "needs_human"), wildcard: true, actor: "ci" },
      ],
    };
    const layout = layoutMap(withWildcard, "inbox");
    expect(layout.path).toEqual(["inbox", "spec", "dev", "pr", "prod"]);
    expect(layout.wildcards).toEqual([{ key: "t-ask", to: "needs_human", actor: "ci" }]);
    // Choosing the wildcard transition opens `t-ask`, not `t-ask:inbox`, which no YAML knows.
    expect(mapEdge(withWildcard, "t-ask")?.id).toBe("t-ask");
  });

  it("does not loop on a cyclic workflow, loses no state, even an isolated one, and stays deterministic", () => {
    const cyclic = {
      nodes: [n("a"), n("b"), n("c"), n("isolated")],
      edges: [nominal("ab", "a", "b"), nominal("bc", "b", "c"), nominal("ca", "c", "a")],
    };
    const first = layoutMap(cyclic);
    expect(first.path).toEqual(["a", "b", "c"]);
    expect(new Set(first.order)).toEqual(new Set(["a", "b", "c", "isolated"]));
    expect(layoutMap(cyclic)).toEqual(first);
  });

  it("eighteen states: thirteen on the path, five beside", () => {
    const { path, sides } = layoutMap(releaseFullValidation.graph!, "inbox");
    expect(path).toHaveLength(13);
    expect(path.at(-1)).toBe("verified_prod");
    expect(sides.map((c) => c.id).sort()).toEqual(["abandoned", "addressing_review", "fixing_ci", "needs_human", "refining"]);
  });

  it("every kind of actor is painted with the console's tokens, and said in so many words", () => {
    for (const { border, fill, text } of Object.values(KIND_COLOURS)) {
      expect(text).toMatch(/^var\(--choregos-[a-z0-9-]+\)$/);
      expect(border).toMatch(/^var\(--choregos-[a-z0-9-]+\)$/);
      expect(fill).toMatch(/^var\(--choregos-[a-z0-9-]+\)$/);
    }
    expect(["agent", "human", "system", "train", "wait", "terminal"].map(actorKind)).toEqual([
      "agent",
      "human",
      "platform",
      "train",
      "waiting",
      "end",
    ]);
    // The map only paints with these tokens: no colour written by hand in its files.
    for (const file of ["workflow-map.tsx", "layout.ts"]) {
      const source = readFileSync(join(import.meta.dirname, "../src/components/workflows/map", file), "utf8");
      expect(source, file).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgb\(/);
    }
  });

  it("edge labels are passed through: only the retry bound is rephrased, a former French label comes out as written", () => {
    expect(secondaryLabel({ from: "a", to: "b", kind: "retry", label: "on failure (≤2)" })).toBe("on failure (up to 2 retries)");
    expect(secondaryLabel({ from: "a", to: "b", kind: "escalate", label: "when retries run out" })).toBe("when retries run out");
    expect(secondaryLabel({ from: "a", to: "b", kind: "reject", label: "rejet" })).toBe("rejet"); // lang: fr-ok - a label written in French passes through
    expect(secondaryLabel({ from: "a", to: "b", kind: "default", label: "question" })).toBe("question");
    expect(defaults(simple)).toEqual([
      { label: "question", to: "NEEDS_HUMAN", from: ["INBOX", "DEV"] },
      { label: "abandon", to: "ABANDONED", from: ["NEEDS_HUMAN"] },
    ]);
  });
});

describe("the flat map: the keyboard (S21-07)", () => {
  const nav = navigation(simple, "inbox");

  it("↓ ↑ follow the reading order: the path, then the column beside", () => {
    expect(nav.order).toEqual(["inbox", "spec", "dev", "pr", "prod", "refining", "needs_human", "abandoned"]);
    expect(nav.move("prod", "ArrowDown")).toBe("refining");
    expect(nav.move("inbox", "ArrowUp")).toBe("inbox");
    expect(nav.move("dev", "End")).toBe("abandoned");
    expect(nav.move("dev", "Home")).toBe("inbox");
  });

  it("→ follows the nominal transition, ← goes back up; at the end, nothing", () => {
    expect(nav.move("spec", "ArrowRight")).toBe("dev");
    expect(nav.move("spec", "ArrowLeft")).toBe("inbox");
    expect(nav.move("prod", "ArrowRight")).toBeNull();
    expect(nav.move("refining", "ArrowRight")).toBe("spec");
  });

  it("describes the state under the cursor: who acts, towards where, and its exits", () => {
    expect(nav.describe("dev")).toBe(
      "DEV (agent): → PR (by dev, gates scope_respected); → DEV on failure (up to 2 retries); → NEEDS_HUMAN when retries run out.",
    );
    expect(nav.describe("prod")).toBe("PROD (end, terminal): no outgoing transition.");
  });
});
