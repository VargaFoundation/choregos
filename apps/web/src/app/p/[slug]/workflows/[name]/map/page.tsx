// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { CarteEnSerpentin } from "@/components/parcours/carte-du-parcours";
import { Card, Empty, ErrorNote } from "@/components/ui";
import { cn } from "@/lib/cn";
import { useBrouillon } from "@/components/workflows/brouillon";
import { CarteDuWorkflow } from "@/components/workflows/carte/carte-du-workflow";
import { areteDeLaCarte, type NoeudDuGraphe } from "@/components/workflows/carte/disposition";
import { timingOfStep } from "@/components/workflows/timing";
import {
  GroupMembersLine,
  openItemsByState,
  stepActivitySentence,
  useWorkflowActivity,
  waitingSentence,
} from "@/components/workflows/step-activity";
import { FormulaireDEtape, PanneauDEtat, PanneauDeTransition } from "@/components/workflows/panneaux";
import { ArtifactFlowCard, type Highlight } from "@/components/workflows/artifact-flow";
import { StepDetails } from "@/components/workflows/step-details";
import { TexteIllisible } from "@/components/workflows/texte-illisible";
import { ActorsPanel } from "@/components/workflows/actors-panel";
import { WorkflowSettingsPanel } from "@/components/workflows/workflow-settings";
import { api } from "@/lib/api";
import { useAgentResolution } from "@/components/workflows/use-agent-resolution";
import { BuiltinAgentOfActor, type AgentActorFields } from "@/components/agents/builtin-agent";

type Definition = {
  initial?: string;
  actors?: Record<string, { type?: string; group?: string; sla_hours?: number; agent?: string } & AgentActorFields>;
  defaults?: { on_timeout?: string };
};

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
  const [flux, setFlux] = useState<Highlight | null>(null);
  const [drawn, setDrawn] = useState<{ from: string; to: string } | null>(null);
  const { slug, name } = useParams<{ slug: string; name: string }>();
  const resolutions = useAgentResolution(slug, brouillon.process);
  const activity = useWorkflowActivity(slug, name);
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  const workflows = useQuery({ queryKey: ["workflows", slug], queryFn: () => api.workflows(slug) });
  if (brouillon.definition.error) return <ErrorNote>{String(brouillon.definition.error)}</ErrorNote>;
  const graph = brouillon.graph;
  if (!graph) return <Empty>The map appears once the workflow is read…</Empty>;
  const noeuds = graph.nodes as NoeudDuGraphe[];
  // The draft's actors, not the published version's: an actor added on the map can carry a step at once.
  const definition = (brouillon.document ?? brouillon.definition.data?.json ?? {}) as Definition;
  const acteurs = Object.keys(definition.actors ?? {});
  const noeud = choix?.kind === "node" ? noeuds.find((n) => n.id === choix.id) : undefined;
  const arete = choix?.kind === "edge" ? areteDeLaCarte(graph, choix.id) : undefined;
  const etapes = brouillon.process ?? [];
  const etapeChoisie = arete ? etapes.find((e) => e.id === arete.id) : undefined;
  // An agent step that names no registry agent: what it runs is said from the actor's own fields.
  const builtinActor =
    etapeChoisie?.actor_type === "agent" && !etapeChoisie.agent
      ? { role: etapeChoisie.role ?? undefined, model: etapeChoisie.model ?? undefined, ...definition.actors?.[etapeChoisie.actor] }
      : undefined;
  const timing = etapeChoisie
    ? timingOfStep(etapeChoisie, { timeoutHours: arete?.timeout_hours, graph, onTimeout: definition.defaults?.on_timeout })
    : null;
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="min-w-0 space-y-4">
      <Card
        title="Map"
        action={
          <span className="inline-flex overflow-hidden rounded border border-line-strong text-xs" role="group" aria-label="View">
            {(["diagram", "list"] as const).map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={vue === v}
                className={cn("px-2.5 py-1", vue === v ? "bg-surface-sunken text-ink" : "text-ink-muted hover:text-ink")}
                onClick={() => router.replace(v === "list" ? `${chemin}?vue=list` : chemin)}
              >
                {v === "list" ? "List" : "Diagram"}
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
              highlight={flux}
              openItems={openItemsByState(activity.data)}
            />
          ) : (
            <CarteDuWorkflow
              graph={graph}
              process={brouillon.process}
              initial={definition.initial}
              acteurs={definition.actors}
              onSelect={(kind, id) => setChoix({ kind, id })}
              onDraw={(from, to) => {
                setChoix(null);
                setDrawn({ from, to });
              }}
            />
          )}
        </div>
      </Card>
      {vue === "diagram" && <ArtifactFlowCard artifacts={graph.artifacts} steps={etapes} onHighlight={setFlux} />}
      </div>
      <div className="space-y-4 self-start lg:sticky lg:top-16" data-testid="panneaux-de-la-carte">
        {drawn && (
          <Card title={`New transition from ${drawn.from} to ${drawn.to}`}>
            <FormulaireDEtape
              key={`${drawn.from}-${drawn.to}`}
              depart={drawn.from}
              initialTarget={drawn.to}
              etats={noeuds}
              acteurs={acteurs}
              onDone={() => setDrawn(null)}
            />
            <button type="button" className="mt-2 text-xs underline" onClick={() => setDrawn(null)}>
              Cancel
            </button>
          </Card>
        )}
        {noeud && <PanneauDEtat key={`n-${noeud.id}`} noeud={noeud} etats={noeuds} acteurs={acteurs} />}
        {arete && etapeChoisie && (
          <Card title="This step">
            <StepDetails
              step={etapeChoisie}
              resolution={etapeChoisie.agent ? resolutions.get(etapeChoisie.agent) : undefined}
              flow={graph.artifacts}
              stepsById={new Map(etapes.map((e) => [e.id, e]))}
            />
            {etapeChoisie.actor_type === "human" && etapeChoisie.group && (
              <GroupMembersLine projectSlug={slug} group={etapeChoisie.group} />
            )}
            {waitingSentence(activity.data, etapeChoisie.from, etapeChoisie.from_display ?? etapeChoisie.from) && (
              <p className="mt-2 text-xs text-ink-muted" data-testid="attente-de-l-etape">
                {waitingSentence(activity.data, etapeChoisie.from, etapeChoisie.from_display ?? etapeChoisie.from)}
              </p>
            )}
            {stepActivitySentence(activity.data, etapeChoisie.id) && (
              <p className="mt-2 text-xs text-ink-muted" data-testid="activite-de-l-etape">
                {stepActivitySentence(activity.data, etapeChoisie.id)}
              </p>
            )}
          </Card>
        )}
        {builtinActor && (
          <Card title="This agent">
            <BuiltinAgentOfActor key={etapeChoisie?.actor} projectSlug={slug} actor={builtinActor} />
          </Card>
        )}
        {arete && (
          <PanneauDeTransition
            key={`e-${arete.id ?? arete.from}`}
            arete={arete}
            acteurs={acteurs}
            definitions={definition.actors}
            gates={etapeChoisie?.gates}
            timing={timing}
            states={noeuds.map((n) => n.id)}
            workflows={(workflows.data ?? []).map((w) => w.name)}
          />
        )}
        {!noeud && !arete && !drawn && (
          <>
            <Empty title="Edit the workflow">
              Choose a step on the diagram, or a state in the list view (click, or Tab then Enter).
            </Empty>
            {/* Ajouter une étape ne demandait pas moins que la vue liste, un état choisi, puis « a new
                state… » dans une liste : introuvable (seconde passe du 08/10, S23-13). */}
            <Card title="Add a step">
              <FormulaireDEtape etats={noeuds} acteurs={acteurs} />
            </Card>
            <ActorsPanel org={project.data?.org ?? ""} />
            <WorkflowSettingsPanel />
          </>
        )}
      </div>
    </div>
  );
}
