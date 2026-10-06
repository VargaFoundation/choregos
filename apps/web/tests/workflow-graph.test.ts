import { describe, expect, it } from "vitest";
import { aretesAffichees, defaults, layout, navigation, ouverture } from "@/components/workflow-graph";
import { renommer } from "@/components/workflows/renommer";

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
    // Une arête secondaire dessinée (en pointillés) : une escalade. Les arêtes `default`, elles, ne
    // se dessinent plus (voir plus bas).
    { id: "d1", from: "in_progress", to: "needs_human", kind: "escalate", label: "question" },
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

describe("les arêtes par défaut (ADR 0031, S16-09)", () => {
  // `from_any_agent_state` : une arête par état d'agent vers le même état — la carte s'y noyait.
  const avecDefauts = {
    nodes: graph.nodes,
    edges: [
      ...graph.edges.filter((edge) => edge.id !== "d1"),
      { id: "a", from: "inbox", to: "needs_human", kind: "default", label: "question" },
      { id: "b", from: "in_progress", to: "needs_human", kind: "default", label: "question" },
      { id: "c", from: "in_progress", to: "needs_human", kind: "default", label: "budget dépassé" },
    ],
  };

  it("ne se dessinent plus, mais placent encore l'état d'escalade", () => {
    const { nodes, edges } = layout(avecDefauts);
    expect(edges.map((edge) => edge.id)).toEqual(["t1", "t2", "t3"]);
    const x = (id: string) => nodes.find((node) => node.id === id)?.position.x ?? -1;
    expect(x("needs_human")).toBeGreaterThan(x("inbox"));
  });

  it("se disent une fois, en légende", () => {
    expect(defaults(avecDefauts)).toEqual([
      { label: "question", to: "Question", from: ["Inbox", "En cours"] },
      { label: "budget dépassé", to: "Question", from: ["En cours"] },
    ]);
  });

  it("ne s'énumèrent pas dans la description d'un état", () => {
    expect(navigation(avecDefauts).describe("in_progress")).toBe("En cours (agent lane): → Fait (by ci).");
  });
});

describe("un workflow neuf, depuis un gabarit", () => {
  it("prend son nom dans `metadata.name`, en flow comme en bloc, sans toucher au reste", () => {
    expect(renommer("metadata: { name: default-simple, version: 1 }\nstates: {}\n", "offboarding")).toBe(
      "metadata: { name: offboarding, version: 1 }\nstates: {}\n",
    );
    expect(renommer("metadata:\n  name: default-simple\n  version: 1\n", "offboarding")).toBe(
      "metadata:\n  name: offboarding\n  version: 1\n",
    );
  });
});


describe("une carte qui se lit (relevé sur le dev le 06/10)", () => {
  /** Deux escalades depuis le même état, vers deux états humains : même couloir, même colonne. */
  const encombre = {
    nodes: [
      { id: "travail", display: "Travail", lane: "agent" },
      { id: "question", display: "Question", lane: "human" },
      { id: "revue", display: "Revue", lane: "human" },
      { id: "fin", display: "Fin", lane: "terminal", terminal: true },
    ],
    edges: [
      { id: "a", from: "travail", to: "fin", kind: "nominal", actor: "ci", gates: ["ci_green", "review_approved", "scans_ok"] },
      { id: "b", from: "travail", to: "question", kind: "escalate", label: "question" },
      { id: "c", from: "travail", to: "revue", kind: "escalate", label: "revue" },
    ],
  };

  it("deux états du même couloir et de la même colonne ne tombent jamais au même point", () => {
    const positions = layout(encombre).nodes.map((node) => `${node.position.x},${node.position.y}`);
    expect(new Set(positions).size).toBe(positions.length);
  });

  it("le couloir qui empile deux états est plus haut que les autres", () => {
    const { lanes } = layout(encombre);
    const hauteur = (lane: string) => lanes.find((couloir) => couloir.lane === lane)?.height ?? 0;
    expect(hauteur("human")).toBeGreaterThan(hauteur("agent"));
    expect(lanes.map((couloir) => couloir.lane)).toEqual(["agent", "human", "terminal"]);
  });

  it("ne peint qu'avec des jetons du design system : `rgb(var(--…))` n'existe plus dans la console", () => {
    const { nodes, edges, lanes } = layout(encombre);
    const peintures = [
      ...nodes.flatMap((node) => [node.style?.border, node.style?.borderLeft, node.style?.background, node.style?.color]),
      ...edges.flatMap((edge) => [edge.style?.stroke, (edge.markerEnd as { color?: string } | undefined)?.color]),
      ...lanes.map((couloir) => couloir.color),
    ].map(String);
    expect(peintures.filter((peinture) => peinture.includes("rgb(var(--"))).toEqual([]);
    expect(peintures.every((peinture) => peinture.includes("var(--varga-"))).toBe(true);
  });

  it("une étiquette trop longue donne le nombre de garanties, pas leurs noms", () => {
    const { edges } = layout(encombre);
    expect(edges.find((edge) => edge.id === "a")?.label).toBe("ci · 3 gates");
  });

  it("les flèches vont de gauche à droite, avec une pointe", () => {
    const { nodes, edges } = layout(encombre);
    expect(nodes.every((node) => node.sourcePosition === "right" && node.targetPosition === "left")).toBe(true);
    expect(edges.every((edge) => edge.markerEnd !== undefined && edge.type === "smoothstep")).toBe(true);
  });

  it("une carte qui tient lisible s'ajuste ; une longue s'ouvre lisible, sur son début", () => {
    expect(ouverture({ width: 1300, height: 480 }, 600, 300)).toBe("ajuster");
    const longue = ouverture({ width: 1300, height: 480 }, 2400, 400);
    expect(longue).toEqual({ x: 8, y: expect.any(Number), zoom: 0.8 });
    expect(ouverture(undefined, 2400, 400)).toBe("ajuster");
    expect(ouverture({ width: 0, height: 0 }, 2400, 400)).toBe("ajuster");
  });
});

describe("le chemin nominal se lit seul ; les reprises se montrent à la demande", () => {
  const cachees = (liste: ReturnType<typeof layout>["edges"]) => liste.filter((edge) => edge.hidden).map((edge) => edge.id);

  it("une escalade est cachée par défaut : treize pointillés croisaient la carte du gabarit", () => {
    expect(cachees(aretesAffichees(layout(graph).edges, false, [null, null]))).toEqual(["d1"]);
  });

  it("elle se montre autour de son état — d'où elle part comme où elle arrive — et pas ailleurs", () => {
    const { edges } = layout(graph);
    expect(cachees(aretesAffichees(edges, false, ["in_progress"]))).toEqual([]);
    expect(cachees(aretesAffichees(edges, false, [null, "needs_human"]))).toEqual([]);
    expect(cachees(aretesAffichees(edges, false, ["inbox"]))).toEqual(["d1"]);
  });

  it("toutes sur demande ; le chemin nominal ne se cache jamais", () => {
    const { edges } = layout(graph);
    expect(cachees(aretesAffichees(edges, true, []))).toEqual([]);
    const nominales = aretesAffichees(edges, false, []).filter((edge) => !edge.data?.secondaire);
    expect(nominales.map((edge) => edge.id)).toEqual(["t1", "t2", "t3"]);
    expect(nominales.every((edge) => !edge.hidden)).toBe(true);
  });

  it("chaque état porte la couleur de son couloir, que reprend la vue d'ensemble", () => {
    const { nodes } = layout(graph);
    expect(nodes.find((node) => node.id === "inbox")?.data.couleur).toBe("var(--varga-accent-strong)");
  });

  it("chaque état donne ses dimensions à React Flow : sans elles, la vue d'ensemble ne dessinait rien", () => {
    for (const node of layout(graph).nodes) {
      expect(node.initialWidth).toBeGreaterThan(0);
      expect(node.initialHeight).toBeGreaterThan(0);
    }
  });
});
