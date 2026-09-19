import { describe, expect, it } from "vitest";
import { layout } from "@/components/workflow-graph";

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
