import { describe, expect, it } from "vitest";
import { layout, navigation } from "@/components/workflow-graph";

const graph = {
  nodes: [
    { id: "inbox", display: "Inbox", lane: "agent" },
    { id: "ready", display: "Prêt", lane: "human" },
    { id: "in_progress", display: "En cours", lane: "agent" },
    { id: "needs_human", display: "Question", lane: "human" },
    { id: "done", display: "Fait", lane: "terminal", terminal: true },
  ],
  edges: [
    { id: "t1", from: "inbox", to: "ready", kind: "nominal", actor: "refiner", gates: [] },
    { id: "t2", from: "ready", to: "in_progress", kind: "nominal", actor: "owner", gates: ["spec_ok"] },
    { id: "t3", from: "in_progress", to: "done", kind: "nominal", actor: "ci", gates: [] },
    { id: "d1", from: "in_progress", to: "needs_human", kind: "default", label: "question" },
  ],
};

describe("disposition du graphe de workflow", () => {
  function position(id: string): { x: number; y: number } {
    const node = layout(graph).nodes.find((candidate) => candidate.id === id);
    if (!node) throw new Error(`état absent du graphe : ${id}`);
    return node.position;
  }

  it("place chaque état dans le couloir de son acteur", () => {
    expect(position("inbox").y).toBe(position("in_progress").y);
    expect(position("ready").y).not.toBe(position("inbox").y);
    expect(layout(graph).nodes).toHaveLength(5);
  });

  it("avance d'une colonne à chaque étape nominale", () => {
    expect(position("inbox").x).toBeLessThan(position("ready").x);
    expect(position("ready").x).toBeLessThan(position("in_progress").x);
    expect(position("in_progress").x).toBeLessThan(position("done").x);
  });

  it("place un état d'escalade après l'état d'où l'on y tombe, pas en première colonne", () => {
    expect(position("needs_human").x).toBeGreaterThan(position("in_progress").x);
  });

  it("est déterministe : deux rendus du même workflow donnent la même carte", () => {
    expect(layout(graph)).toEqual(layout(graph));
  });

  it("distingue les arêtes secondaires en pointillés", () => {
    const { edges } = layout(graph);
    const secondaire = edges.find((edge) => edge.id === "d1");
    const nominale = edges.find((edge) => edge.id === "t1");
    expect(secondaire?.style?.strokeDasharray).toBeDefined();
    expect(nominale?.style?.strokeDasharray).toBeUndefined();
    expect(secondaire?.label).toBe("question");
  });

  it("montre l'acteur et les gates sur les arêtes nominales", () => {
    const { edges } = layout(graph);
    expect(edges.find((edge) => edge.id === "t2")?.label).toBe("owner · spec_ok");
  });

  it("ne boucle pas sur un workflow cyclique", () => {
    const cyclique = {
      nodes: [
        { id: "a", display: "A", lane: "agent" },
        { id: "b", display: "B", lane: "agent" },
      ],
      edges: [
        { id: "x", from: "a", to: "b", kind: "nominal" },
        { id: "y", from: "b", to: "a", kind: "nominal" },
      ],
    };
    const { nodes } = layout(cyclique);
    expect(nodes).toHaveLength(2);
  });

  it("ne perd aucun état, même isolé", () => {
    const isole = {
      nodes: [{ id: "orphelin", display: "Orphelin", lane: "wait" }],
      edges: [],
    };
    expect(layout(isole).nodes.map((node) => node.id)).toEqual(["orphelin"]);
  });
});

describe("parcours du graphe au clavier", () => {
  const nav = navigation(graph);

  it("Tab suit l'ordre de lecture : colonne par colonne, puis couloir par couloir", () => {
    expect(nav.order).toEqual(["inbox", "ready", "in_progress", "needs_human", "done"]);
    expect(layout(graph).nodes.map((node) => node.id)).toEqual(nav.order);
  });

  it("→ suit la transition nominale, ← la remonte", () => {
    expect(nav.move("inbox", "ArrowRight")).toBe("ready");
    expect(nav.move("ready", "ArrowRight")).toBe("in_progress");
    expect(nav.move("in_progress", "ArrowRight")).toBe("done");
    expect(nav.move("done", "ArrowLeft")).toBe("in_progress");
  });

  it("→ sans transition nominale prend l'arête secondaire, et reste sur place au bout", () => {
    expect(nav.move("needs_human", "ArrowLeft")).toBe("in_progress");
    expect(nav.move("done", "ArrowRight")).toBeNull();
    expect(nav.move("inbox", "ArrowLeft")).toBeNull();
  });

  it("↑ ↓ passent d'un état à l'autre, Début/Fin sautent aux extrémités", () => {
    expect(nav.move("inbox", "ArrowDown")).toBe("ready");
    expect(nav.move("ready", "ArrowUp")).toBe("inbox");
    expect(nav.move("inbox", "ArrowUp")).toBe("inbox");
    expect(nav.move("ready", "Home")).toBe("inbox");
    expect(nav.move("ready", "End")).toBe("done");
  });

  it("laisse passer les touches qu'il ne connaît pas, et les états inconnus", () => {
    expect(nav.move("inbox", "Enter")).toBeUndefined();
    expect(nav.move("inbox", "Tab")).toBeUndefined();
    expect(nav.move("fantome", "ArrowRight")).toBeUndefined();
  });

  it("décrit l'état sous le curseur avec ses transitions, acteurs et gates", () => {
    expect(nav.describe("ready")).toBe("Prêt (human lane): → En cours (by owner, gates spec_ok).");
    expect(nav.describe("in_progress")).toBe("En cours (agent lane): → Fait (by ci); → Question on question.");
    expect(nav.describe("done")).toBe("Fait (terminal lane, terminal): no outgoing transition.");
    expect(nav.describe("fantome")).toBe("");
  });

  it("nomme chaque état pour le lecteur d'écran", () => {
    const { nodes } = layout(graph);
    expect(nodes.find((node) => node.id === "in_progress")?.ariaLabel).toBe(
      "En cours, agent lane, 2 outgoing transitions",
    );
    expect(nodes.find((node) => node.id === "done")?.ariaLabel).toBe("Fait, terminal lane, 0 outgoing transitions");
  });
});
