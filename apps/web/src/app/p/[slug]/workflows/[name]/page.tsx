// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import {
  useParams,
  usePathname,
  useRouter,
  useSearchParams,
} from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { SnakeMap } from "@/components/journey/journey-map";
import { Button, Card, Drawer, Empty, ErrorNote } from "@/components/ui";
import { Cog, Link2, Plus, Users } from "lucide-react";
import { cn } from "@/lib/cn";
import { useDraft } from "@/components/workflows/draft";
import { WorkflowMap } from "@/components/workflows/map/workflow-map";
import {
  mapEdge,
  type GraphNode,
} from "@/components/workflows/map/layout";
import { timingOfStep } from "@/components/workflows/timing";
import {
  GroupMembersLine,
  openItemsByState,
  stepActivitySentence,
  useWorkflowActivity,
  waitingSentence,
} from "@/components/workflows/step-activity";
import {
  StepForm,
  StatePanel,
  TransitionPanel,
} from "@/components/workflows/panels";
import {
  ArtifactFlowCard,
  type Highlight,
} from "@/components/workflows/artifact-flow";
import { StepDetails } from "@/components/workflows/step-details";
import { decisionActorOf } from "@/components/workflows/document";
import { workflowThresholds } from "@/components/workflows/decision-calibration";
import { UnreadableText } from "@/components/workflows/unreadable-text";
import { ActorsPanel } from "@/components/workflows/actors-panel";
import { WorkflowSettingsPanel } from "@/components/workflows/workflow-settings";
import { api } from "@/lib/api";
import { useAgentResolution } from "@/components/workflows/use-agent-resolution";
import {
  BuiltinAgentOfActor,
  type AgentActorFields,
} from "@/components/agents/builtin-agent";

type Definition = {
  initial?: string;
  actors?: Record<
    string,
    {
      type?: string;
      group?: string;
      sla_hours?: number;
      agent?: string;
    } & AgentActorFields
  >;
  defaults?: { on_timeout?: string };
};

/**
 * The map, and what is changed on it (S16-12): a chosen state or transition opens its panel; each
 * gesture is a typed operation the API grafts into the YAML, and the map redraws from the draft. Nothing
 * is published before "publish". Since S21-07 the map is flat: one column for the nominal path, one beside
 * it for what leaves it; the panel stays within reach as one scrolls. Since S22-05 it opens as a
 * serpentine, like a ticket's journey (captures of 08/10); the flat list is one tab away (`?view=list`),
 * to edit the states.
 */
export default function MapPage() {
  const draft = useDraft();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  // `vue` was the French name of `view` (S55-04): an old link still opens the list.
  const vue =
    (params.get("view") ?? params.get("vue")) === "list" ? "list" : "diagram";
  const [choix, setChoix] = useState<{
    kind: "node" | "edge";
    id: string;
  } | null>(null);
  const [flux, setFlux] = useState<Highlight | null>(null);
  // A transition being drawn: dragged in the list view (both ends known), or started with
  // "Connect to…" from a chosen node (S55-13), the target then picked in the form.
  const [drawn, setDrawn] = useState<{ from: string; to?: string } | null>(
    null,
  );
  const drawnCard = useRef<HTMLDivElement>(null);
  // "Connect to…" is reached by the keyboard (ADR 0112 §6): the form it opens takes the focus.
  useEffect(() => {
    if (drawn && !drawn.to) drawnCard.current?.querySelector("select")?.focus();
  }, [drawn]);
  // What the toolbar opened (S55-06): each tool in a drawer, so the graph keeps the width.
  const [tool, setTool] = useState<"step" | "actors" | "settings" | null>(null);
  const { slug, name } = useParams<{ slug: string; name: string }>();
  const resolutions = useAgentResolution(slug, draft.process);
  const activity = useWorkflowActivity(slug, name);
  const project = useQuery({
    queryKey: ["project", slug],
    queryFn: () => api.project(slug),
  });
  const workflows = useQuery({
    queryKey: ["workflows", slug],
    queryFn: () => api.workflows(slug),
  });
  if (draft.definition.error)
    return <ErrorNote>{String(draft.definition.error)}</ErrorNote>;
  const graph = draft.graph;
  if (!graph) return <Empty>The map appears once the workflow is read…</Empty>;
  const noeuds = graph.nodes as GraphNode[];
  // The draft's actors, not the published version's: an actor added on the map can carry a step at once.
  const definition = (draft.document ??
    draft.definition.data?.json ??
    {}) as Definition;
  const acteurs = Object.keys(definition.actors ?? {});
  const graphNode =
    choix?.kind === "node" ? noeuds.find((n) => n.id === choix.id) : undefined;
  const arete =
    choix?.kind === "edge" ? mapEdge(graph, choix.id) : undefined;
  const steps = draft.process ?? [];
  // Where "Connect to…" starts (S55-13): the state the step chosen on the diagram leaves, where its
  // work items wait — "Add a step after this one" already starts from its arrival, and a state chosen
  // in the list view holds the same form in its own panel. Never from a terminal state.
  const connectFrom =
    vue === "diagram" && arete
      ? noeuds.find((n) => n.id === arete.from && !n.terminal)
      : undefined;
  const chosenStep = arete
    ? steps.find((e) => e.id === arete.id)
    : undefined;
  // An agent step that names no registry agent: what it runs is said from the actor's own fields.
  const builtinActor =
    chosenStep?.actor_type === "agent" && !chosenStep.agent
      ? {
          role: chosenStep.role ?? undefined,
          model: chosenStep.model ?? undefined,
          ...definition.actors?.[chosenStep.actor],
        }
      : undefined;
  const timing = chosenStep
    ? timingOfStep(chosenStep, {
        timeoutHours: arete?.timeout_hours,
        graph,
        onTimeout: definition.defaults?.on_timeout,
      })
    : null;
  return (
    <div className="space-y-4">
      {/* Editing starts here (S55-06): the three gestures that change the whole workflow, always in
          sight; a step or an arrow of the graph opens its own panel on the right. */}
      <div
        className="flex flex-wrap items-center gap-2"
        role="toolbar"
        aria-label="Edit the workflow"
      >
        <Button tone="accent" size="sm" onClick={() => setTool("step")}>
          <Plus aria-hidden className="size-4" /> Add a step
        </Button>
        <Button size="sm" onClick={() => setTool("actors")}>
          <Users aria-hidden className="size-4" /> Actors
          <span className="font-mono text-xs text-ink-muted">
            {acteurs.length}
          </span>
        </Button>
        <Button size="sm" onClick={() => setTool("settings")}>
          <Cog aria-hidden className="size-4" /> Workflow settings
        </Button>
        <span className="text-xs text-ink-muted">
          Every change goes to a draft; nothing runs before you publish it.
        </span>
      </div>
      <Drawer
        open={tool === "step"}
        onClose={() => setTool(null)}
        title="Add a step"
      >
        {tool === "step" && (
          <StepForm
            states={noeuds}
            actors={acteurs}
            onDone={() => setTool(null)}
          />
        )}
      </Drawer>
      <Drawer
        open={tool === "actors"}
        onClose={() => setTool(null)}
        title="Actors"
      >
        {tool === "actors" && <ActorsPanel org={project.data?.org ?? ""} />}
      </Drawer>
      <Drawer
        open={tool === "settings"}
        onClose={() => setTool(null)}
        title="Workflow settings"
      >
        {tool === "settings" && <WorkflowSettingsPanel />}
      </Drawer>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-4">
          <Card
            title="Graph"
            action={
              <span
                className="inline-flex overflow-hidden rounded border border-line-strong text-xs"
                role="group"
                aria-label="View"
              >
                {(["diagram", "list"] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    aria-pressed={vue === v}
                    className={cn(
                      "px-2.5 py-1",
                      vue === v
                        ? "bg-surface-sunken text-ink"
                        : "text-ink-muted hover:text-ink",
                    )}
                    onClick={() =>
                      router.replace(
                        v === "list" ? `${pathname}?view=list` : pathname,
                      )
                    }
                  >
                    {v === "list" ? "List" : "Diagram"}
                  </button>
                ))}
              </span>
            }
          >
            <div
              className={
                draft.graphStale ? "space-y-3 opacity-60" : "space-y-3"
              }
            >
              {draft.graphStale && <UnreadableText />}
              {vue === "diagram" ? (
                <SnakeMap
                  graph={graph}
                  process={draft.process ?? []}
                  initial={definition.initial}
                  selected={choix?.kind === "edge" ? choix.id : null}
                  onSelect={(id) => {
                    setDrawn(null);
                    setChoix({ kind: "edge", id });
                  }}
                  highlight={flux}
                  openItems={openItemsByState(activity.data)}
                />
              ) : (
                <WorkflowMap
                  graph={graph}
                  process={draft.process}
                  initial={definition.initial}
                  actors={definition.actors}
                  onSelect={(kind, id) => {
                    setDrawn(null);
                    setChoix({ kind, id });
                  }}
                  onDraw={(from, to) => {
                    setChoix(null);
                    setDrawn({ from, to });
                  }}
                />
              )}
            </div>
          </Card>
          {vue === "diagram" && (
            <ArtifactFlowCard
              artifacts={graph.artifacts}
              steps={steps}
              onHighlight={setFlux}
            />
          )}
        </div>
        <div
          className="space-y-4 self-start lg:sticky lg:top-16"
          data-testid="map-panels"
        >
          {drawn && (
            <Card
              title={
                drawn.to
                  ? `New transition from ${drawn.from} to ${drawn.to}`
                  : `New transition from ${drawn.from}`
              }
              data-testid="drawn-transition"
            >
              <div ref={drawnCard}>
                <StepForm
                  key={`${drawn.from}-${drawn.to ?? ""}`}
                  from={drawn.from}
                  initialTarget={drawn.to}
                  states={noeuds}
                  actors={acteurs}
                  onDone={() => setDrawn(null)}
                />
              </div>
              <button
                type="button"
                className="mt-2 text-xs underline"
                onClick={() => setDrawn(null)}
              >
                Cancel
              </button>
            </Card>
          )}
          {connectFrom && drawn?.from !== connectFrom.id && (
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                onClick={() => setDrawn({ from: connectFrom.id })}
                aria-describedby="connect-from"
              >
                <Link2 aria-hidden className="size-4" /> Connect to…
              </Button>
              <span id="connect-from" className="text-xs text-ink-muted">
                A new transition out of{" "}
                <span data-verbatim>{connectFrom.display}</span>
              </span>
            </div>
          )}
          {graphNode && (
            <StatePanel
              key={`n-${graphNode.id}`}
              node={graphNode}
              states={noeuds}
              actors={acteurs}
            />
          )}
          {arete && chosenStep && (
            <Card title="This step">
              <StepDetails
                step={chosenStep}
                resolution={
                  chosenStep.agent
                    ? resolutions.get(chosenStep.agent)
                    : undefined
                }
                flow={graph.artifacts}
                stepsById={new Map(steps.map((e) => [e.id, e]))}
                decision={decisionActorOf(draft.document, chosenStep.actor)}
                calibration={{ project: slug, workflow: name, thresholds: workflowThresholds(draft.document, chosenStep.to) }}
              />
              {chosenStep.actor_type === "human" && chosenStep.group && (
                <GroupMembersLine
                  projectSlug={slug}
                  group={chosenStep.group}
                />
              )}
              {waitingSentence(
                activity.data,
                chosenStep.from,
                chosenStep.from_display ?? chosenStep.from,
              ) && (
                <p
                  className="mt-2 text-xs text-ink-muted"
                  data-testid="step-waiting"
                >
                  {waitingSentence(
                    activity.data,
                    chosenStep.from,
                    chosenStep.from_display ?? chosenStep.from,
                  )}
                </p>
              )}
              {stepActivitySentence(activity.data, chosenStep.id) && (
                <p
                  className="mt-2 text-xs text-ink-muted"
                  data-testid="step-activity"
                >
                  {stepActivitySentence(activity.data, chosenStep.id)}
                </p>
              )}
            </Card>
          )}
          {builtinActor && (
            <Card title="This agent">
              <BuiltinAgentOfActor
                key={chosenStep?.actor}
                projectSlug={slug}
                actor={builtinActor}
              />
            </Card>
          )}
          {arete && (
            <TransitionPanel
              key={`e-${arete.id ?? arete.from}`}
              edge={arete}
              actors={acteurs}
              definitions={definition.actors}
              gates={chosenStep?.gates}
              timing={timing}
              states={noeuds.map((n) => n.id)}
              workflows={(workflows.data ?? []).map((w) => w.name)}
            />
          )}
          {/* Building on the graph (S55-09): from the step one is looking at, the next one — its
              arrival state prefilled, the target and the actor chosen in the form. */}
          {arete && !noeuds.find((n) => n.id === arete.to)?.terminal && (
            <Card title="Add a step after this one" data-testid="step-after-step">
              <StepForm
                key={`after-${arete.id ?? arete.from}`}
                from={arete.to}
                states={noeuds}
                actors={acteurs}
              />
            </Card>
          )}
          {!graphNode && !arete && !drawn && (
            <Empty title="Change a step">
              Choose a step on the graph to see what it does, change it, add
              the step that follows it, or connect it to another state (click,
              or Tab then Enter). In the list view, drag a state onto another
              to draw a new transition.
            </Empty>
          )}
        </div>
      </div>
    </div>
  );
}
