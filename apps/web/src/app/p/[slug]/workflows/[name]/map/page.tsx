// SPDX-License-Identifier: Apache-2.0
"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { CarteEnSerpentin } from "@/components/parcours/carte-du-parcours";
import { Card, Empty, ErrorNote } from "@/components/ui";
import { cn } from "@/lib/cn";
import { useBrouillon } from "@/components/workflows/brouillon";
import { CarteDuWorkflow } from "@/components/workflows/carte/carte-du-workflow";
import { areteDeLaCarte, type NoeudDuGraphe } from "@/components/workflows/carte/disposition";
import { FormulaireDEtape, PanneauDEtat, PanneauDeTransition } from "@/components/workflows/panneaux";
import { TexteIllisible } from "@/components/workflows/texte-illisible";

type Definition = { initial?: string; actors?: Record<string, { type?: string; group?: string; sla_hours?: number }> };

/**
 * La carte, et ce qu'on y modifie (S16-12) : un état ou une transition choisis ouvrent leur panneau ;
 * chaque geste est une opération typée, greffée dans le YAML par l'API, et la carte se redessine
 * depuis le brouillon. Rien n'est publié avant « publish ». Depuis S21-07, la carte est à plat : une
 * colonne pour le chemin nominal, une à côté pour ce qui en sort ; le panneau reste à portée quand on
 * la descend. Depuis S22-05, elle s'ouvre en serpentin, comme le parcours d'un ticket (les captures
 * du 08/10) ; la liste à plat reste à un onglet (`?vue=list`), pour éditer les états.
 */
export default function MapPage() {
  const brouillon = useBrouillon();
  const params = useSearchParams();
  const router = useRouter();
  const chemin = usePathname();
  const vue = params.get("vue") === "list" ? "list" : "diagram";
  const [choix, setChoix] = useState<{ kind: "node" | "edge"; id: string } | null>(null);
  if (brouillon.definition.error) return <ErrorNote>{String(brouillon.definition.error)}</ErrorNote>;
  const graph = brouillon.graph;
  if (!graph) return <Empty>the map appears once the workflow is read…</Empty>;
  const noeuds = graph.nodes as NoeudDuGraphe[];
  const definition = (brouillon.definition.data?.json ?? {}) as Definition;
  const acteurs = Object.keys(definition.actors ?? {});
  const noeud = choix?.kind === "node" ? noeuds.find((n) => n.id === choix.id) : undefined;
  const arete = choix?.kind === "edge" ? areteDeLaCarte(graph, choix.id) : undefined;
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <Card
        title="map"
        action={
          <span className="inline-flex overflow-hidden rounded border border-line-strong text-xs" role="group" aria-label="view">
            {(["diagram", "list"] as const).map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={vue === v}
                className={cn("px-2.5 py-1", vue === v ? "bg-surface-sunken text-ink" : "text-ink-muted hover:text-ink")}
                onClick={() => router.replace(v === "list" ? `${chemin}?vue=list` : chemin)}
              >
                {v}
              </button>
            ))}
          </span>
        }
      >
        <div className={brouillon.grapheObsolete ? "space-y-3 opacity-60" : "space-y-3"}>
          {brouillon.grapheObsolete && <TexteIllisible />}
          {vue === "diagram" ? (
            <CarteEnSerpentin
              graph={graph}
              process={brouillon.process ?? []}
              initial={definition.initial}
              choix={choix?.kind === "edge" ? choix.id : null}
              onChoisir={(id) => setChoix({ kind: "edge", id })}
            />
          ) : (
            <CarteDuWorkflow
              graph={graph}
              process={brouillon.process}
              initial={definition.initial}
              acteurs={definition.actors}
              onSelect={(kind, id) => setChoix({ kind, id })}
            />
          )}
        </div>
      </Card>
      <div className="space-y-4 self-start lg:sticky lg:top-20" data-testid="panneaux-de-la-carte">
        {noeud && <PanneauDEtat key={`n-${noeud.id}`} noeud={noeud} etats={noeuds} acteurs={acteurs} />}
        {arete && <PanneauDeTransition key={`e-${arete.id ?? arete.from}`} arete={arete} acteurs={acteurs} />}
        {!noeud && !arete && (
          <>
            <Empty title="edit the workflow">
              choose a step on the diagram, or a state in the list view (click, or Tab then Enter).
            </Empty>
            {/* Ajouter une étape ne demandait pas moins que la vue liste, un état choisi, puis « a new
                state… » dans une liste : introuvable (seconde passe du 08/10, S23-13). */}
            <Card title="add a step">
              <FormulaireDEtape etats={noeuds} acteurs={acteurs} />
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
