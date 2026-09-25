"use client";

import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import { useMemo, useRef, useState, type FocusEvent, type KeyboardEvent } from "react";
import "@xyflow/react/dist/style.css";

type GraphNode = {
  id: string;
  display: string;
  lane?: string;
  terminal?: boolean;
  tracker_status?: string | null;
};

type GraphEdge = {
  id?: string;
  from: string;
  to: string;
  kind?: string;
  label?: string;
  actor?: string | null;
  gates?: string[];
};

/** Un couloir par type d'acteur : on lit le workflow de haut en bas, comme un plan de voie. */
const LANES = ["agent", "human", "system", "train", "wait", "terminal"] as const;

const LANE_COLOR: Record<string, string> = {
  agent: "rgb(var(--agent))",
  human: "rgb(var(--human))",
  system: "rgb(var(--system))",
  train: "rgb(var(--ok))",
  wait: "rgb(var(--warn))",
  terminal: "rgb(var(--ink-muted))",
};

const LANE_HEIGHT = 110;
const COLUMN_WIDTH = 210;

/**
 * Graphe du workflow en couloirs. La disposition est **déterministe** : deux validations
 * du même YAML donnent la même carte, sinon relire un diff de workflow serait un jeu de
 * piste. Les arêtes secondaires (rejet, reprise, escalade, défauts) sont en pointillés.
 *
 * Le graphe se parcourt **au clavier** : Tab atteint les états dans l'ordre de lecture
 * (colonne par colonne), ← et → suivent les transitions, ↑ et ↓ passent d'un état à
 * l'autre, Début/Fin sautent aux extrémités. L'état sous le curseur est décrit sous la
 * carte, avec ses transitions sortantes — c'est cette phrase qu'un lecteur d'écran lit.
 */
export function WorkflowGraph({ graph }: { graph: { nodes: unknown[]; edges: unknown[] } }) {
  const { nodes, edges } = useMemo(() => layout(graph), [graph]);
  const nav = useMemo(() => navigation(graph), [graph]);
  const [focused, setFocused] = useState<string | null>(null);
  const container = useRef<HTMLDivElement>(null);

  function focusNode(id: string) {
    const element = container.current?.querySelector<HTMLElement>(
      `.react-flow__node[data-id="${CSS.escape(id)}"]`,
    );
    element?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const current = (event.target as HTMLElement).closest<HTMLElement>(".react-flow__node")?.dataset.id;
    if (!current) return;
    const target = nav.move(current, event.key);
    if (target === undefined) return;
    event.preventDefault();
    event.stopPropagation();
    if (target !== null) focusNode(target);
  }

  function onFocus(event: FocusEvent<HTMLDivElement>) {
    const id = (event.target as HTMLElement).closest<HTMLElement>(".react-flow__node")?.dataset.id;
    setFocused(id ?? null);
  }

  return (
    <div className="space-y-2">
      <div
        ref={container}
        className="h-[28rem] w-full rounded border border-line bg-surface"
        data-testid="workflow-graph"
        onKeyDownCapture={onKeyDown}
        onFocusCapture={onFocus}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          fitView
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
          edgesFocusable={false}
        >
          <Background gap={20} />
          <MiniMap pannable zoomable className="!bg-surface-muted" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <p className="text-xs text-ink-muted" aria-live="polite" data-testid="workflow-graph-focus">
        {focused ? nav.describe(focused) : "Tab reaches the states; ← → follow transitions, ↑ ↓ change state, Home/End jump to the ends."}
      </p>
    </div>
  );
}

export function layout(graph: { nodes: unknown[]; edges: unknown[] }): { nodes: Node[]; edges: Edge[] } {
  const raw = graph.nodes as GraphNode[];
  const rawEdges = graph.edges as GraphEdge[];
  const columns = depths(raw, rawEdges);
  const perLane = new Map<string, number>();

  const nodes: Node[] = raw.map((node) => {
    const lane = node.lane ?? "system";
    const row = LANES.indexOf(lane as (typeof LANES)[number]);
    const column = columns.get(node.id) ?? perLane.get(lane) ?? 0;
    perLane.set(lane, column + 1);
    const outgoing = rawEdges.filter((edge) => edge.from === node.id).length;
    return {
      id: node.id,
      position: { x: column * COLUMN_WIDTH, y: (row < 0 ? LANES.length : row) * LANE_HEIGHT },
      data: { label: node.display },
      ariaLabel: `${node.display}, ${lane} lane, ${outgoing} outgoing transition${outgoing === 1 ? "" : "s"}`,
      draggable: false,
      style: {
        width: 170,
        borderRadius: 6,
        border: `1px solid ${LANE_COLOR[lane] ?? LANE_COLOR.system}`,
        background: "rgb(var(--surface-muted))",
        color: "rgb(var(--ink))",
        fontSize: 12,
        padding: 8,
        fontWeight: node.terminal ? 600 : 400,
      },
    } satisfies Node;
  });

  const edges: Edge[] = rawEdges.map((edge, index) => {
    const secondary = Boolean(edge.kind && edge.kind !== "nominal");
    return {
      id: edge.id ?? `${edge.from}->${edge.to}-${index}`,
      source: edge.from,
      target: edge.to,
      label: secondary ? edge.label : [edge.actor, edge.gates?.join(", ")].filter(Boolean).join(" · "),
      animated: false,
      style: {
        stroke: secondary ? "rgb(var(--ink-muted))" : "rgb(var(--agent))",
        strokeDasharray: secondary ? "4 3" : undefined,
        opacity: secondary ? 0.7 : 1,
      },
      labelStyle: { fontSize: 10, fill: "rgb(var(--ink-muted))" },
    } satisfies Edge;
  });

  // Ordre de lecture = ordre du DOM = ordre de Tab : colonne par colonne, puis couloir
  // par couloir. Sans ce tri, Tab suivrait l'ordre du YAML, qui n'est pas celui de la carte.
  nodes.sort((a, b) => a.position.x - b.position.x || a.position.y - b.position.y);
  return { nodes, edges };
}

/** Parcours au clavier : où mène chaque touche depuis un état, et comment le décrire. */
export function navigation(graph: { nodes: unknown[]; edges: unknown[] }): {
  order: string[];
  move: (from: string, key: string) => string | null | undefined;
  describe: (id: string) => string;
} {
  const raw = graph.nodes as GraphNode[];
  const rawEdges = graph.edges as GraphEdge[];
  const order = layout(graph).nodes.map((node) => node.id);
  const byId = new Map(raw.map((node) => [node.id, node]));

  // → suit d'abord une transition nominale ; à défaut, n'importe quelle arête sortante.
  // ← fait de même à rebours. Le premier candidat dans l'ordre du workflow gagne : le
  // parcours est déterministe, comme la carte.
  function follow(from: string, direction: "out" | "in"): string | null {
    const candidates = rawEdges.filter((edge) => (direction === "out" ? edge.from : edge.to) === from);
    const pick = candidates.find((edge) => !edge.kind || edge.kind === "nominal") ?? candidates[0];
    if (!pick) return null;
    return direction === "out" ? pick.to : pick.from;
  }

  function move(from: string, key: string): string | null | undefined {
    const index = order.indexOf(from);
    if (index < 0) return undefined;
    switch (key) {
      case "ArrowRight":
        return follow(from, "out");
      case "ArrowLeft":
        return follow(from, "in");
      case "ArrowDown":
        return order[Math.min(index + 1, order.length - 1)] ?? null;
      case "ArrowUp":
        return order[Math.max(index - 1, 0)] ?? null;
      case "Home":
        return order[0] ?? null;
      case "End":
        return order[order.length - 1] ?? null;
      default:
        return undefined;
    }
  }

  function describe(id: string): string {
    const node = byId.get(id);
    if (!node) return "";
    const outgoing = rawEdges.filter((edge) => edge.from === id);
    const head = `${node.display} (${node.lane ?? "system"} lane${node.terminal ? ", terminal" : ""})`;
    if (outgoing.length === 0) return `${head}: no outgoing transition.`;
    const steps = outgoing.map((edge) => {
      const target = byId.get(edge.to)?.display ?? edge.to;
      const secondary = Boolean(edge.kind && edge.kind !== "nominal");
      if (secondary) return `${target} on ${edge.label ?? edge.kind}`;
      const via = [edge.actor ? `by ${edge.actor}` : null, edge.gates?.length ? `gates ${edge.gates.join(", ")}` : null]
        .filter(Boolean)
        .join(", ");
      return via ? `${target} (${via})` : target;
    });
    return `${head}: → ${steps.join("; → ")}.`;
  }

  return { order, move, describe };
}

/** Profondeur depuis l'état initial, en suivant les arêtes nominales : une colonne par étape. */
function depths(nodes: GraphNode[], edges: GraphEdge[]): Map<string, number> {
  const nominal = edges.filter((edge) => !edge.kind || edge.kind === "nominal");
  // Un état de départ n'est atteint par rien — pas même par une arête secondaire.
  const incoming = new Set(edges.map((edge) => edge.to));
  const roots = nodes.filter((node) => !incoming.has(node.id)).map((node) => node.id);
  const start = roots.length > 0 ? roots : nodes.slice(0, 1).map((node) => node.id);

  const depth = new Map<string, number>(start.map((id) => [id, 0]));
  // Parcours en largeur borné : un workflow cyclique ne doit pas figer la page.
  let frontier = [...start];
  for (let step = 0; step < nodes.length && frontier.length > 0; step += 1) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const edge of nominal.filter((candidate) => candidate.from === id)) {
        const candidate = (depth.get(id) ?? 0) + 1;
        if (!depth.has(edge.to) || candidate > (depth.get(edge.to) ?? 0)) {
          depth.set(edge.to, candidate);
          next.push(edge.to);
        }
      }
    }
    frontier = next;
  }
  // Un état atteint seulement par une arête secondaire (question, rejet, escalade) se place
  // après l'état d'où l'on y tombe, pas en première colonne comme un orphelin.
  for (let step = 0; step < nodes.length; step += 1) {
    let placed = false;
    for (const edge of edges) {
      if (depth.has(edge.to) || !depth.has(edge.from)) continue;
      depth.set(edge.to, (depth.get(edge.from) ?? 0) + 1);
      placed = true;
    }
    if (!placed) break;
  }
  for (const node of nodes) if (!depth.has(node.id)) depth.set(node.id, 0);
  return depth;
}
