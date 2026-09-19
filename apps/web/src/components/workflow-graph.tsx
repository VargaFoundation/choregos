"use client";

import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import { useMemo } from "react";
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
 */
export function WorkflowGraph({ graph }: { graph: { nodes: unknown[]; edges: unknown[] } }) {
  const { nodes, edges } = useMemo(() => layout(graph), [graph]);
  return (
    <div className="h-[28rem] w-full rounded border border-line bg-surface" data-testid="workflow-graph">
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
    return {
      id: node.id,
      position: { x: column * COLUMN_WIDTH, y: (row < 0 ? LANES.length : row) * LANE_HEIGHT },
      data: { label: node.display },
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

  return { nodes, edges };
}

/** Profondeur depuis l'état initial, en suivant les arêtes nominales : une colonne par étape. */
function depths(nodes: GraphNode[], edges: GraphEdge[]): Map<string, number> {
  const nominal = edges.filter((edge) => !edge.kind || edge.kind === "nominal");
  const incoming = new Set(nominal.map((edge) => edge.to));
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
  for (const node of nodes) if (!depth.has(node.id)) depth.set(node.id, 0);
  return depth;
}
