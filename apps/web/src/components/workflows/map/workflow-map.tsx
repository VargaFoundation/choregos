// SPDX-License-Identifier: Apache-2.0
"use client";

import { Diamond } from "lucide-react";
import { useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { LEGENDE } from "@/components/ui";
import { toSentenceCase } from "@/lib/format";
import { cn } from "@/lib/cn";
import type { ProcessStep } from "@/lib/types";
import {
  type GraphEdge,
  howReached,
  KIND_COLOURS,
  defaults,
  layoutMap,
  actorKind,
  type Graph,
  KIND_LABEL,
  navigation,
  type GraphNode,
  exitsOf,
} from "./layout";

type Actors = Record<string, { type?: string; group?: string; sla_hours?: number; role?: string } | undefined>;
type OnSelect = (kind: "node" | "edge", id: string) => void;

/** Beyond this, a box names the number of gates, not each one: the box stays readable. */
const NAMED_GATES_MAX = 3;

/**
 * A workflow's map, laid flat (S21-07): the nominal path in one column, from top to bottom; every
 * state is a box coloured by WHO moves the work item on, and says it in so many words; an arrow
 * between two boxes is the transition, clickable. What leaves the path (retries, rejections,
 * escalations, human waits) is lined up in the column beside it, with where one falls in from. No
 * zoom, no drag: it reads like a page, even at eighteen states, and it prints.
 *
 * With the keyboard: Tab reaches the boxes and the arrows; ↓ ↑ follow the reading order, → ← the
 * nominal transition, Home/End the extremities, Enter opens the panel. The state under the cursor is
 * described under the map (`aria-live`).
 */
/** What a dragged state carries: its name, nothing a page outside the map would understand. */
const DRAG_TYPE = "application/x-choregos-state";

export function WorkflowMap({
  graph,
  process,
  initial,
  actors = {},
  onSelect,
  onDraw,
}: {
  graph: Graph;
  process?: ProcessStep[];
  initial?: string | null;
  actors?: Actors;
  onSelect?: OnSelect;
  /** Dropping a state on another (ADR 0112 §4): the caller opens the add-transition form, nothing is written. */
  onDraw?: (from: string, to: string) => void;
}) {
  const layout = useMemo(() => layoutMap(graph, initial), [graph, initial]);
  const nav = useMemo(() => navigation(graph, initial), [graph, initial]);
  const byId = useMemo(() => new Map((graph.nodes as GraphNode[]).map((n) => [n.id, n])), [graph]);
  const processSteps = useMemo(() => new Map((process ?? []).map((e) => [e.id, e])), [process]);
  const [focus, setFocus] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  // The visible text names each state and arrow (axe `label-content-name-mismatch`); the rest is a hidden description.
  const descriptionsId = useId();
  const around = hover ?? focus;
  // The targets of the hovered state's exits light up in the column beside it.
  const lit = new Set(
    around ? exitsOf(graph, around).secondary.map((a) => a.to) : [],
  );

  function focusState(id: string) {
    container.current?.querySelector<HTMLElement>(`[data-testid="state-${CSS.escape(id)}"]`)?.focus();
  }

  function onKeyboard(event: KeyboardEvent<HTMLButtonElement>) {
    const current = (event.target as HTMLElement).closest<HTMLElement>("[data-state-id]")?.dataset.stateId;
    if (!current) return;
    const target = nav.move(current, event.key);
    if (target === undefined) return;
    event.preventDefault();
    if (target) focusState(target);
  }

  const secondaryCount = (graph.edges as GraphEdge[]).filter((a) => a.kind && a.kind !== "nominal" && a.kind !== "default").length;
  const name = (id: string) => byId.get(id)?.display ?? id;

  const card = (id: string, next?: string, side?: { how: string; id: string }[]) => {
    const node = byId.get(id);
    if (!node) return null;
    const kind = actorKind(node.lane);
    const colour = KIND_COLOURS[kind];
    const { main, others, secondary: exits } = exitsOf(graph, id, next);
    const processStep = main?.id ? processSteps.get(main.id) : undefined;
    const actor = main?.actor ? actors[main.actor] : undefined;
    const rejection = exits.find((a) => a.kind === "reject");
    const showExits = all || around === id;
    const gates = main?.gates ?? [];
    return (
      // Hovering is only a shortcut: the focus of the box's button shows the same exits, with the keyboard.
      // eslint-disable-next-line jsx-a11y/no-static-element-interactions
      <div
        className={cn("raised border border-l-[3px] border-line px-3 py-2 text-sm", side && "border-dashed")}
        data-kind={kind}
        style={{
          borderLeftColor: colour.border,
          borderLeftStyle: "solid",
          outline: lit.has(id) ? "2px solid var(--choregos-focus)" : undefined,
        }}
        onMouseEnter={() => setHover(id)}
        onMouseLeave={() => setHover(null)}
      >
        <button
          type="button"
          data-testid={`state-${id}`}
          data-state-id={id}
          className="block w-full text-left focus-visible:outline-2"
          aria-describedby={node.terminal ? `${descriptionsId}-state-${id}` : undefined}
          onFocus={() => setFocus(id)}
          onKeyDown={onKeyboard}
          onBlur={() => setFocus(null)}
          onClick={() => onSelect?.("node", id)}
          draggable={!!onDraw && !node.terminal}
          onDragStart={(event) => event.dataTransfer.setData(DRAG_TYPE, id)}
          onDragOver={(event) => {
            if (onDraw && event.dataTransfer.types.includes(DRAG_TYPE)) event.preventDefault();
          }}
          onDrop={(event) => {
            const from = event.dataTransfer.getData(DRAG_TYPE);
            if (onDraw && from && from !== id) {
              event.preventDefault();
              onDraw(from, id);
            }
          }}
        >
          <span className="flex items-baseline justify-between gap-2 text-xs">
            <span className={cn(LEGENDE, "inline-flex items-center gap-1 font-medium")} style={{ color: colour.text }}>
              {kind === "decision" && <Diamond aria-hidden className="size-3" strokeWidth={2} data-testid={`decision-${id}`} />}
              {toSentenceCase(KIND_LABEL[kind])}
              {main?.actor ? ` · ${main.actor}` : ""}
            </span>
            {actor?.type === "decision" && actor.role && <span className="text-ink-muted">{actor.role}</span>}
            {actor?.group && (
              <span className="text-ink-muted">
                {actor.group}
                {actor.sla_hours ? `, within ${actor.sla_hours} h` : ""}
              </span>
            )}
          </span>
          <span data-title className="mt-0.5 block text-[15px] font-semibold text-ink">
            {node.display}
          </span>
          <span className="block font-mono text-xs text-ink-muted">{id}</span>
          {node.terminal && (
            <span id={`${descriptionsId}-state-${id}`} hidden>
              Terminal state
            </span>
          )}
        </button>
        {(processStep?.outputs?.length || gates.length || main?.timeout_hours || rejection) && (
          <ul className="mt-1.5 space-y-0.5 text-xs text-ink-muted">
            {processStep?.outputs && processStep.outputs.length > 0 && <li>Produces {processStep.outputs.join(", ")}</li>}
            {gates.length > 0 && (
              <li title={gates.join(", ")}>
                Only if {gates.length > NAMED_GATES_MAX ? `${gates.length} gates hold` : gates.join(", ")}
              </li>
            )}
            {main?.timeout_hours ? <li>Times out after {main.timeout_hours} h</li> : null}
            {rejection && <li>If rejected → {name(rejection.to)}</li>}
          </ul>
        )}
        {others.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {others.map((a) => (
              <button
                key={a.id ?? a.to}
                type="button"
                data-testid={`transition-${a.id}`}
                className="border border-line bg-surface px-1.5 py-0.5 font-mono text-[11px] hover:border-line-strong pointer-coarse:min-h-11"
                onClick={() => a.id && onSelect?.("edge", a.id)}
              >
                Also → {name(a.to)}
                {a.actor ? ` (by ${a.actor})` : ""}
              </button>
            ))}
          </div>
        )}
        {exits.length > 0 && showExits && (
          <ul className="mt-1.5 space-y-0.5 border-t border-line pt-1.5 text-xs text-retrying-ink" data-testid={`secondary-${id}`}>
            {exits.map((a) => (
              <li key={a.id ?? `${a.to}-${a.kind}`}>
                {toSentenceCase(howReached(a))} → {name(a.to)}
              </li>
            ))}
          </ul>
        )}
        {side && side.length > 0 && (
          <p className="mt-1.5 text-xs text-ink-muted">
            Reached from {side.map((d) => `${name(d.id)} (${d.how})`).join("; ")}
          </p>
        )}
      </div>
    );
  };

  const arrow = (from: string, to: string) => {
    const { main } = exitsOf(graph, from, to);
    if (!main) return null;
    const who = main.actor ? `by ${main.actor}` : main.via ? "by the release train" : "";
    const gates = main.gates?.length ? `${main.gates.length} gate${main.gates.length > 1 ? "s" : ""}` : "";
    const effect = processSteps.get(main.id ?? "")?.who.includes("opens the pull request")
      ? "opens the PR"
      : processSteps.get(main.id ?? "")?.who.includes("merges the pull request")
        ? "merges the PR"
        : "";
    return (
      <button
        type="button"
        data-testid={`transition-${main.id}`}
        className="group mx-auto flex flex-col items-center py-1 text-xs text-ink-muted hover:text-ink"
        aria-describedby={`${descriptionsId}-transition-${main.id}`}
        onClick={() => main.id && onSelect?.("edge", main.id)}
      >
        <span id={`${descriptionsId}-transition-${main.id}`} hidden>
          Transition {main.id}: {name(from)} to {name(to)}
        </span>
        <span aria-hidden className="h-3 w-px bg-line-strong group-hover:bg-ink" />
        <span className="px-1.5 font-mono text-[11px]">{toSentenceCase([who, gates, effect].filter(Boolean).join(" · ")) || main.id}</span>
        <span aria-hidden className="leading-none">
          ▼
        </span>
      </button>
    );
  };

  const description = focus ? nav.describe(focus) : "Tab reaches the states and the arrows; arrows move along the workflow; Enter opens one.";

  return (
    <div className="space-y-4" data-testid="workflow-graph">
      <MapLegend graph={graph} all={all} onAll={setAll} secondaryCount={secondaryCount} wildcards={layout.wildcards} name={name} onSelect={onSelect} />
      {/* The keyboard arrows are read on every box (`onKeyboard`); Tab stays free. */}
      <div
        ref={container}
        role="group"
        aria-label="Workflow map"
        className="grid gap-x-6 gap-y-3 md:grid-cols-[minmax(0,1fr)_17rem]"
      >
        <ol aria-label="Nominal path" className="space-y-0">
          {layout.path.map((id, i) => (
            <li key={id}>
              {card(id, layout.path[i + 1])}
              {layout.path[i + 1] && arrow(id, layout.path[i + 1]!)}
            </li>
          ))}
        </ol>
        <div className="space-y-2">
          {layout.sides.length > 0 && <p className={LEGENDE}>Off the main path</p>}
          <ul aria-label="Off the main path" className="space-y-2">
            {layout.sides.map((cote) => (
              <li key={cote.id}>{card(cote.id, undefined, cote.reachedFrom)}</li>
            ))}
          </ul>
        </div>
      </div>
      <p className="text-xs text-ink-muted" aria-live="polite" data-testid="workflow-graph-focus">
        {description}
      </p>
    </div>
  );
}

function MapLegend({
  graph,
  all,
  onAll,
  secondaryCount,
  wildcards,
  name,
  onSelect,
}: {
  graph: Graph;
  all: boolean;
  onAll: (v: boolean) => void;
  secondaryCount: number;
  wildcards: { key: string; to: string; actor?: string | null }[];
  name: (id: string) => string;
  onSelect?: OnSelect;
}) {
  const nodes = graph.nodes as GraphNode[];
  const counts = new Map<string, number>();
  for (const n of nodes) {
    const kind = actorKind(n.lane);
    counts.set(kind, (counts.get(kind) ?? 0) + 1);
  }
  const fallbacks = defaults(graph);
  return (
    <div className="space-y-2 text-xs text-ink-muted" data-testid="workflow-legend">
      <ul className="flex flex-wrap gap-x-4 gap-y-1" aria-label="Who moves the work item">
        {(Object.keys(KIND_COLOURS) as (keyof typeof KIND_COLOURS)[])
          .filter((kind) => counts.has(kind))
          .map((kind) => (
            <li key={kind} className="inline-flex items-center gap-1.5">
              <span aria-hidden className="inline-block h-3 w-[3px]" style={{ background: KIND_COLOURS[kind].border }} />
              {toSentenceCase(KIND_LABEL[kind])} · {counts.get(kind)}
            </li>
          ))}
      </ul>
      {fallbacks.length > 0 && (
        <div>
          <p className="text-ink">From any agent state</p>
          <ul className="mt-1 space-y-0.5" data-testid="workflow-defaults">
            {fallbacks.map((repli) => (
              <li key={`${repli.label}->${repli.to}`}>
                {repli.label} → {repli.to}
                <span className="sr-only"> (from {repli.from.join(", ")})</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {wildcards.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {wildcards.map((wildcard) => (
            <button
              key={wildcard.key}
              type="button"
              data-testid={`transition-${wildcard.key}`}
              className="border border-line bg-surface px-1.5 py-0.5 font-mono text-[11px] hover:border-line-strong pointer-coarse:min-h-11"
              onClick={() => onSelect?.("edge", wildcard.key)}
            >
              From any agent step → {name(wildcard.to)}
              {wildcard.actor ? ` (by ${wildcard.actor})` : ""}
            </button>
          ))}
        </div>
      )}
      {secondaryCount > 0 && (
        <label className="inline-flex items-center gap-2">
          <input type="checkbox" checked={all} onChange={(event) => onAll(event.target.checked)} />
          Show every retry, rejection and escalation ({secondaryCount}) — or hover a state for its own
        </label>
      )}
    </div>
  );
}
