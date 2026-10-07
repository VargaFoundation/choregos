// SPDX-License-Identifier: Apache-2.0
"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import { Card, Empty, ErrorNote } from "@/components/ui";
import { TexteIllisible } from "@/components/workflows/texte-illisible";
import { WorkflowLegend, areteDessinee } from "@/components/workflow-graph";
import { useBrouillon } from "@/components/workflows/brouillon";
import { PanneauDEtat, PanneauDeTransition } from "@/components/workflows/panneaux";

// React Flow pèse lourd : chargé à l'ouverture de la carte, et d'elle seule.
const WorkflowGraph = dynamic(() => import("@/components/workflow-graph").then((m) => m.WorkflowGraph), {
  ssr: false,
  loading: () => <p className="text-sm text-ink-muted">rendering the map…</p>,
});

type Noeud = { id: string; display: string; terminal?: boolean; kind?: string };
type Arete = { id?: string; from: string; to: string; kind?: string; actor?: string | null; gates?: string[] };

/**
 * La carte, et ce qu'on y modifie (S16-12) : un état ou une transition choisis ouvrent leur panneau ;
 * chaque geste est une opération typée, greffée dans le YAML par l'API, et la carte se redessine
 * depuis le brouillon. Rien n'est publié avant « publish ».
 */
export default function MapPage() {
  const brouillon = useBrouillon();
  const [choix, setChoix] = useState<{ kind: "node" | "edge"; id: string } | null>(null);
  if (brouillon.definition.error) return <ErrorNote>{String(brouillon.definition.error)}</ErrorNote>;
  const graph = brouillon.graph;
  if (!graph) return <Empty>the map appears once the workflow is read…</Empty>;
  const noeuds = graph.nodes as Noeud[];
  const acteurs = Object.keys(brouillon.definition.data?.json?.actors ?? {});
  const noeud = choix?.kind === "node" ? noeuds.find((n) => n.id === choix.id) : undefined;
  const arete = choix?.kind === "edge" ? (areteDessinee(graph, choix.id) as Arete | undefined) : undefined;
  return (
    <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
      <Card title="map">
        <div className={brouillon.grapheObsolete ? "space-y-3 opacity-60" : "space-y-3"}>
          {brouillon.grapheObsolete && <TexteIllisible />}
          <WorkflowLegend graph={graph} />
          <WorkflowGraph graph={graph} onSelect={(kind, id) => setChoix({ kind, id })} />
        </div>
      </Card>
      <div className="space-y-4">
        {noeud && <PanneauDEtat key={`n-${noeud.id}`} noeud={noeud} etats={noeuds} acteurs={acteurs} />}
        {arete && <PanneauDeTransition key={`e-${arete.id ?? arete.from}`} arete={arete} acteurs={acteurs} />}
        {!noeud && !arete && (
          <Empty title="edit the workflow">choose a state (click, or Enter) or a transition on the map.</Empty>
        )}
      </div>
    </div>
  );
}
