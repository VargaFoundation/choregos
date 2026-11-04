// SPDX-License-Identifier: Apache-2.0
"use client";

import { type RefObject, useEffect, useId, useMemo, useRef, useState } from "react";
import { buttonClasses } from "@/components/ui";
import { KIND_COLOURS } from "@/components/workflows/map/layout";
import { cn } from "@/lib/cn";
import { toSentenceCase, relative, shortDate, usd } from "@/lib/format";
import type { WorkItemJourney } from "@/lib/types";
import { place, RADIUS, truncate, type Placement, type Point } from "./layout";
import { useReader, type Speed } from "./reader";
import {
  atInstant,
  phaseStart,
  type Step,
  type StepState,
  instants,
  type Instant,
  buildModel,
  type JourneyModel,
  narrate,
  outputsOf,
  type StepStatus,
  structure,
} from "./model";
import { SENT_BACK_COLOUR, STATUS_COLOUR as COLOUR, statusFill } from "./colours";
import { StepPanel } from "./step-panel";

/** What a status says, in so many words: colour never carries the meaning alone. */
export const STATUS_LABEL: Record<StepStatus, string> = {
  a_venir: "not reached",
  en_cours: "in progress",
  attend: "waiting for a person",
  fait: "done",
  renvoye: "sent back",
  echoue: "failed",
};

const STATUS_LEGEND: [StepStatus, string][] = [
  ["en_cours", "in progress"],
  ["attend", "waiting for a person"],
  ["renvoye", "sent back"],
  ["fait", "done"],
  ["echoue", "failed"],
];

const SPEEDS: Speed[] = [0.5, 1, 2];

/**
 * A work item's journey, animated (S22-02; review of 08/10: "dynamic, readable... and for every
 * agent one can see the logs and actions").
 *
 * The workflow's path snakes from left to right then from right to left; every box is a step
 * carried by an agent (circle), a person (circle and silhouette), the platform (square) or the
 * train (pill). What runs pulses, what waits for someone too, more slowly; a review that sends
 * back draws an orange arc and counts its rounds; a detour (review rework, CI repair) hangs under
 * its row. Live, the map follows the API; "Replay" plays the work item's life back event by
 * event, at the chosen speed, and a clicked phase jumps there. Clicking a box opens its panel:
 * every attempt, what the agent did, its log.
 */
export function JourneyMap({
  journey,
  slug,
  itemId,
  initialWidth = 1100,
  onDecided,
  decisionAnchor,
}: {
  journey: WorkItemJourney;
  slug: string;
  itemId: string;
  initialWidth?: number;
  onDecided?: () => void;
  /** The page already shows the decision under this anchor: a waiting step leads there instead of
   * offering a second set of buttons (S55-05). */
  decisionAnchor?: string;
}) {
  const model = useMemo(() => buildModel(journey), [journey]);
  const frames = useMemo(() => instants(journey), [journey]);
  const reader = useReader(frames.length);
  const t = reader.mode === "live" ? Number.POSITIVE_INFINITY : (frames[reader.index] ?? Number.POSITIVE_INFINITY);
  const instant = useMemo(() => atInstant(journey, model, t), [journey, model, t]);
  const [selected, setSelected] = useState<string | null>(null);
  const [frame, width] = useWidth(initialWidth);
  const placement = useMemo(() => place(model, width), [model, width]);
  const selectedStep = selected ? model.byId.get(selected) : undefined;
  const story = reader.mode === "replay" ? narrate(model, instant.latest) : "";

  return (
    <section className="space-y-3" data-testid="parcours" aria-label={`Journey of ${journey.tracker_key ?? "the work item"}`}>
      <Header journey={journey} model={model} instant={instant} />
      <ReaderBar reader={reader} frames={frames} story={story} />
      <div className={cn("grid gap-4", selectedStep && "xl:grid-cols-[minmax(0,1fr)_26rem]")}>
        <div ref={frame} className="min-w-0">
          <Diagram
            model={model}
            placement={placement}
            instant={instant}
            itemKey={journey.tracker_key ?? ""}
            selected={selected}
            onSelect={(id) => setSelected(id === selected ? null : id)}
          />
          <Exceptions model={model} instant={instant} />
        </div>
        {selectedStep && (
          <StepPanel
            step={selectedStep}
            state={instant.steps.get(selectedStep.id)!}
            process={journey.process.find((p) => p.id === selectedStep.id)}
            slug={slug}
            itemId={itemId}
            onClose={() => setSelected(null)}
            onDecided={onDecided}
            decisionAnchor={decisionAnchor}
          />
        )}
      </div>
      <Phases journey={journey} model={model} instant={instant} frames={frames} onGoTo={reader.goTo} />
    </section>
  );
}

const KIND_LEGEND: [Step["kind"], string][] = [
  ["agent", "Agent"],
  ["human", "Person"],
  ["platform", "Platform"],
  ["train", "Release train"],
];

/** A decision step (ADR 0121) is named in the legend only where the workflow has one. */
const DECISION_LEGEND: [Step["kind"], string] = ["decision", "Decision"];

/**
 * A workflow's map, without a work item (S22-05; "the display is very linear, unlike the
 * screenshots"): the same snake as a work item's journey (loops as arcs, detours under their row,
 * one shape per carrier), but nothing moves: every box takes the colour of who carries it.
 * Clicking a box returns its transition identifier, which the editing page opens in its panel.
 */
export function SnakeMap({
  graph,
  process,
  initial,
  selected = null,
  onSelect,
  initialWidth = 1100,
  highlight,
  openItems,
}: {
  graph: WorkItemJourney["graph"];
  process: WorkItemJourney["process"];
  initial?: string | null;
  selected?: string | null;
  onSelect?: (transitionId: string) => void;
  initialWidth?: number;
  /** The boxes an artifact row lights up: who produces it, who reads it (S39-13). Decoration only. */
  highlight?: Map<string, "produces" | "reads"> | null;
  /** How many open work items wait in each state (S47-39), written after the state's name. */
  openItems?: Map<string, number> | null;
}) {
  const model = useMemo(() => buildModel({ graph, process: process ?? [], initial: initial ?? null }), [graph, process, initial]);
  const instant = useMemo(() => structure(model), [model]);
  const [frame, width] = useWidth(initialWidth);
  const placement = useMemo(() => place(model, width, true), [model, width]);
  return (
    <section className="space-y-3" data-testid="carte-en-serpentin" aria-label="Workflow diagram">
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted" aria-label="Who carries each step">
        {[
          ...KIND_LEGEND,
          ...([...model.byId.values()].some((e) => e.kind === "decision") ? [DECISION_LEGEND] : []),
        ].map(([kind, label]) => (
          <li key={kind} className="inline-flex items-center gap-1.5">
            <span
              aria-hidden
              className="inline-block size-3 rounded-full border-2"
              style={{ borderColor: KIND_STROKE[kind] }}
            />
            {label}
          </li>
        ))}
        <li className="inline-flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-0.5 w-4 border-t-2 border-dashed border-line-strong" />
          sent back, or a detour
        </li>
      </ul>
      <div ref={frame} className="min-w-0">
        <Diagram
          model={model}
          placement={placement}
          instant={instant}
          itemKey=""
          selected={selected}
          onSelect={(id) => onSelect?.(id)}
          structural
          flow={highlight ?? undefined}
          openItems={openItems ?? undefined}
        />
        <Exceptions model={model} instant={instant} />
      </div>
    </section>
  );
}

/** The frame's width, followed: the map changes its number of columns, it does not overflow. */
function useWidth(initial: number): [RefObject<HTMLDivElement | null>, number] {
  const frame = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(initial);
  useEffect(() => {
    const element = frame.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const measured = Math.floor(entry?.contentRect.width ?? 0);
      if (measured > 0) setWidth(measured);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [frame, width];
}

function Header({ journey, model, instant }: { journey: WorkItemJourney; model: JourneyModel; instant: Instant }) {
  const cost = journey.steps.reduce((sum, s) => sum + (s.cost_usd ?? 0), 0);
  const activeSteps = model.steps.concat(model.detours.map((d) => d.step)).filter((e) => {
    const s = instant.steps.get(e.id)?.status;
    return s === "en_cours" || s === "attend";
  });
  return (
    <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm" data-testid="parcours-maintenant">
      <span className="text-ink-muted" data-donnee>
        {journey.workflow_name} v{journey.workflow_version}
      </span>
      <span>
        Now in <strong>{model.name(instant.state)}</strong>
        {activeSteps.length > 0 && (
          <span className="text-ink-muted">
            {" "}
            ·{" "}
            {activeSteps
              .map((e) => `${e.label} ${instant.steps.get(e.id)?.status === "attend" ? "awaited" : "working"}`)
              .join(", ")}
          </span>
        )}
      </span>
      <span className="text-ink-muted">· {usd(cost)} spent by agents</span>
    </p>
  );
}

function ReaderBar({ reader, frames, story }: { reader: ReturnType<typeof useReader>; frames: number[]; story: string }) {
  const live = reader.mode === "live";
  const button = buttonClasses("secondary", "sm", "gap-1.5");
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2" role="toolbar" aria-label="Replay the journey">
        {reader.playing ? (
          <button
            type="button"
            aria-label="Pause the replay"
            className={buttonClasses("primary", "sm", "gap-1.5")}
            onClick={reader.pause}
          >
            <span aria-hidden>❚❚</span> Pause
          </button>
        ) : (
          <button
            type="button"
            className={buttonClasses("primary", "sm", "gap-1.5")}
            onClick={reader.play}
            disabled={frames.length === 0}
            aria-label={live ? "Replay the journey" : "Play the replay"}
          >
            <span aria-hidden>▶</span> {live ? "Replay" : "Play"}
          </button>
        )}
        <button
          type="button"
          className={button}
          onClick={reader.restart}
          disabled={frames.length === 0}
          aria-label="Restart the replay"
        >
          <span aria-hidden>↺</span> restart
        </button>
        <span className="inline-flex overflow-hidden border border-line-strong" role="group" aria-label="Speed">
          {SPEEDS.map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={reader.speed === v}
              className={cn(
                "h-7 px-2 font-mono text-xs pointer-coarse:min-h-11",
                reader.speed === v ? "bg-accent-soft text-accent-strong" : "text-ink-muted hover:text-ink",
              )}
              onClick={() => reader.setSpeed(v)}
            >
              {v}×
            </button>
          ))}
        </span>
        <button
          type="button"
          aria-pressed={live}
          className={cn(button, live && "border-running-line bg-running-soft text-running-ink hover:bg-running-soft")}
          onClick={reader.live}
          aria-label="Live: follow the work item"
        >
          <span aria-hidden className={cn("inline-block size-1.5 rounded-full bg-current", live && "parcours-pouls-point")} />
          Live
        </button>
        {!live && frames.length > 0 && (
          <label className="ml-1 inline-flex min-w-48 flex-1 items-center gap-2 text-xs text-ink-muted">
            <input
              type="range"
              min={0}
              max={frames.length - 1}
              value={reader.index}
              onChange={(event) => reader.goTo(Number(event.target.value))}
              className="flex-1 accent-[var(--choregos-accent)]"
              aria-label="Moment of the journey"
            />
            <span className="whitespace-nowrap font-mono" data-testid="parcours-moment">
              {reader.index + 1} / {frames.length} · {shortDate(new Date(frames[reader.index]!).toISOString())}
            </span>
          </label>
        )}
        <ul className="ml-auto flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-muted" aria-label="Legend">
          {STATUS_LEGEND.map(([status, label]) => (
            <li key={status} className="inline-flex items-center gap-1.5">
              <StatusDot status={status} />
              {label}
            </li>
          ))}
        </ul>
      </div>
      <p className={cn("min-h-5 text-sm", story ? "text-ink" : "text-ink-muted")} aria-live="polite" data-testid="parcours-recit">
        {story || (live ? "Live: the map follows the work item. Replay plays its life back, event by event." : "")}
      </p>
    </div>
  );
}

/**
 * A status dot in the legend, drawn like the map's box (S23-12): a coloured stroke was not enough:
 * "in progress" (turquoise) and "done" (green) were mixed up. What lives wears a ring around it,
 * what runs is filled; what is done wears neither.
 */
function StatusDot({ status }: { status: StepStatus }) {
  const alive = status === "en_cours" || status === "attend";
  return (
    <span
      aria-hidden
      className="inline-flex size-4 shrink-0 items-center justify-center rounded-full border"
      style={{ borderColor: alive ? COLOUR[status] : "transparent" }}
    >
      <span
        className="size-2.5 rounded-full border-2"
        style={{
          borderColor: COLOUR[status],
          background: statusFill(status),
        }}
      />
    </span>
  );
}

function Diagram({
  model,
  placement,
  instant,
  itemKey,
  selected,
  onSelect,
  structural = false,
  flow,
  openItems,
}: {
  model: JourneyModel;
  placement: Placement;
  instant: Instant;
  itemKey: string;
  selected: string | null;
  onSelect: (id: string) => void;
  /** A workflow's map without a work item: every box takes the colour of who carries it (S22-05). */
  structural?: boolean;
  flow?: Map<string, "produces" | "reads">;
  openItems?: Map<string, number>;
}) {
  const allSteps = useMemo(() => [...model.steps, ...model.detours.map((d) => d.step)], [model]);
  const finished = instant.state === model.path.at(-1);
  // The arrow heads of the arcs: one for the taken arc (orange), one for the structure (grey).
  const arrowId = useId().replace(/:/g, "");
  return (
    <div className="overflow-x-auto">
      <div
        className="relative"
        style={{ height: placement.height, width: placement.width }}
        role="group"
        aria-label="The work item's path"
      >
        <svg aria-hidden className="absolute inset-0 overflow-visible" width={placement.width} height={placement.height}>
          <defs>
            {(
              [
                ["taken", SENT_BACK_COLOUR],
                ["structure", "var(--choregos-line-strong)"],
              ] as const
            ).map(([name, colour]) => (
              <marker
                key={name}
                id={`${arrowId}-${name}`}
                viewBox="0 0 10 10"
                refX={8}
                refY={5}
                markerWidth={7}
                markerHeight={7}
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill={colour} />
              </marker>
            ))}
          </defs>
          {placement.segments.map((segment, k) => {
            const active = instant.state === segment.state && !finished;
            const travelled = instant.visited.has(segment.state) && !active;
            const label = segment.label;
            return (
              <g
                key={`${segment.state}-${k}`}
                data-segment={segment.state}
                data-statut={active ? "actif" : travelled ? "parcouru" : "a_venir"}
              >
                <path
                  d={segment.d}
                  fill="none"
                  stroke={active ? COLOUR.en_cours : travelled ? "var(--choregos-ink-muted)" : "var(--choregos-line-strong)"}
                  strokeWidth={active || travelled ? 3 : 2}
                  strokeLinecap="round"
                  // Once crossed, the stretch is drawn: `pathLength` brings its length to 1, the animation does the rest.
                  pathLength={active || travelled ? 1 : undefined}
                  strokeDasharray={active || travelled ? 1 : undefined}
                  className={cn("parcours-trait", (active || travelled) && "parcours-trace")}
                />
                {active && (
                  <circle r={4.5} fill={COLOUR.en_cours} className="parcours-anime">
                    <animateMotion dur="1.8s" repeatCount="indefinite" path={segment.d} />
                  </circle>
                )}
                <text
                  stroke="var(--choregos-surface)"
                  strokeWidth={4}
                  paintOrder="stroke"
                  x={label.x}
                  y={label.y}
                  textAnchor={label.anchor}
                  fontSize={11}
                  fontWeight={active ? 600 : 400}
                  fill={active ? COLOUR.en_cours : "var(--choregos-ink-muted)"}
                >
                  <title>{model.name(segment.state)}</title>
                  {truncate(
                    active
                      ? `now · ${model.name(segment.state)}`
                      : openItems?.get(segment.state)
                        ? `${openItems.get(segment.state)} open · ${model.name(segment.state)}`
                        : model.name(segment.state),
                    label.maxWidth,
                  )}
                </text>
              </g>
            );
          })}
          {model.arcs.map((arc) => {
            const route = placement.arcs.get(arc.key);
            if (!route) return null;
            const taken = instant.arcs.get(arc.key) ?? {
              times: 0,
              latest: false,
            };
            return (
              <g key={arc.key} data-arc={arc.key} data-fois={taken.times}>
                <path
                  d={route.d}
                  fill="none"
                  stroke={taken.times > 0 ? SENT_BACK_COLOUR : "var(--choregos-line-strong)"}
                  strokeWidth={taken.times > 0 ? 2.5 : 1.5}
                  strokeDasharray={taken.times > 0 ? undefined : "4 5"}
                  opacity={taken.times > 0 ? 1 : 0.55}
                  markerEnd={`url(#${arrowId}-${taken.times > 0 ? "taken" : "structure"})`}
                  className="parcours-trait"
                />
                {taken.latest && (
                  <circle r={4} fill={SENT_BACK_COLOUR} className="parcours-anime">
                    <animateMotion dur="1.4s" repeatCount="indefinite" path={route.d} />
                  </circle>
                )}
                {/* A workflow's map says why each dashed arc exists ("if rejected"); a work item's says
                    the ones it took. */}
                {((taken.times > 0 && arc.kind === "loop") || (structural && arc.kind !== "back")) && (
                  <text
                    stroke="var(--choregos-surface)"
                    strokeWidth={4}
                    paintOrder="stroke"
                    x={route.label.x}
                    y={route.label.y}
                    textAnchor="middle"
                    fontSize={11}
                    fontWeight={600}
                    fill={taken.times > 0 ? SENT_BACK_COLOUR : "var(--choregos-ink-muted)"}
                    data-testid={`arc-label-${arc.key}`}
                  >
                    {arc.label}
                    {taken.times > 1 ? ` ×${taken.times}` : ""}
                  </text>
                )}
              </g>
            );
          })}
          <EndMark point={placement.start} />
          <EndMark point={placement.end} atteinte={finished} />
          {allSteps.map((step) => {
            const point = placement.steps.get(step.id);
            const state = instant.steps.get(step.id);
            if (!point || !state) return null;
            return (
              <Box key={step.id} step={step} state={state} point={point} chosen={selected === step.id} structural={structural} />
            );
          })}
        </svg>
        <Caption
          point={placement.start}
          width={placement.width / placement.columns}
          title="Work item in"
          subtitle={structural ? model.name(model.path[0] ?? "") : itemKey}
          offset={30}
        />
        <Caption
          point={placement.end}
          width={placement.width / placement.columns}
          title={finished ? `${model.name(model.path.at(-1) ?? "")} ✓` : model.name(model.path.at(-1) ?? "")}
          subtitle="The end of the path"
          offset={30}
        />
        {allSteps.map((step) => {
          const point = placement.steps.get(step.id);
          const state = instant.steps.get(step.id);
          if (!point || !state) return null;
          return (
            <StepButton
              key={step.id}
              step={step}
              state={state}
              point={point}
              width={placement.width / placement.columns}
              chosen={selected === step.id}
              onSelect={() => onSelect(step.id)}
              structural={structural}
              flow={flow?.get(step.id)}
            />
          );
        })}
      </div>
    </div>
  );
}

function EndMark({ point, atteinte = false }: { point: Point; atteinte?: boolean }) {
  return (
    <rect
      x={point.x - 3}
      y={point.y - 20}
      width={6}
      height={40}
      rx={3}
      fill={atteinte ? COLOUR.fait : "var(--choregos-ink-muted)"}
    />
  );
}

/**
 * A box's shape: an agent's circle, a person's silhouette, the platform's square, the train's
 * pill. Without a work item, the colour says who carries the step, the one of the list-view map:
 * plum for the agent, blue for the person, slate for the platform, green for the train.
 */
const KIND_STROKE: Record<Step["kind"], string> = {
  agent: KIND_COLOURS.agent.border,
  human: KIND_COLOURS.human.border,
  platform: KIND_COLOURS.platform.border,
  train: KIND_COLOURS.train.border,
  decision: KIND_COLOURS.decision.border,
};

function Box({
  step,
  state,
  point,
  chosen,
  structural = false,
}: {
  step: Step;
  state: StepState;
  point: Point;
  chosen: boolean;
  structural?: boolean;
}) {
  const colour = structural ? KIND_STROKE[step.kind] : COLOUR[state.status];
  const alive = state.status === "en_cours" || state.status === "attend";
  const filled = structural || state.status !== "a_venir";
  const fill = structural ? "var(--choregos-surface)" : statusFill(state.status);
  const { x, y } = point;
  return (
    <g data-etape={step.id} data-statut={state.status}>
      {alive && (
        <circle
          cx={x}
          cy={y}
          r={RADIUS + 4}
          fill="none"
          stroke={colour}
          strokeWidth={2}
          className={state.status === "attend" ? "parcours-pouls-lent" : "parcours-pouls"}
        />
      )}
      {chosen && <circle cx={x} cy={y} r={RADIUS + 7} fill="none" stroke="var(--choregos-focus)" strokeWidth={2} />}
      {step.kind === "platform" ? (
        <rect
          x={x - RADIUS + 1}
          y={y - RADIUS + 1}
          width={2 * RADIUS - 2}
          height={2 * RADIUS - 2}
          rx={5}
          fill={fill}
          stroke={colour}
          strokeWidth={3}
          className="parcours-forme"
        />
      ) : step.kind === "decision" ? (
        // A decision is a diamond, the shape a flowchart gives a question (ADR 0121).
        <polygon
          points={`${x},${y - RADIUS - 2} ${x + RADIUS + 2},${y} ${x},${y + RADIUS + 2} ${x - RADIUS - 2},${y}`}
          fill={fill}
          stroke={colour}
          strokeWidth={3}
          strokeLinejoin="round"
          className="parcours-forme"
        />
      ) : step.kind === "train" ? (
        <rect
          x={x - RADIUS - 7}
          y={y - RADIUS + 2}
          width={2 * RADIUS + 14}
          height={2 * RADIUS - 4}
          rx={RADIUS - 2}
          fill={fill}
          stroke={colour}
          strokeWidth={3}
          className="parcours-forme"
        />
      ) : (
        <circle cx={x} cy={y} r={RADIUS} fill={fill} stroke={colour} strokeWidth={3} className="parcours-forme" />
      )}
      {step.kind === "human" ? (
        <g fill={filled ? colour : "var(--choregos-ink-muted)"}>
          <circle cx={x} cy={y - 3.5} r={3.4} />
          <path d={`M ${x - 6.5} ${y + 7} Q ${x} ${y - 1.5} ${x + 6.5} ${y + 7} Z`} />
        </g>
      ) : step.kind === "train" ? (
        <g fill={filled ? colour : "var(--choregos-ink-muted)"}>
          {[-7, 0, 7].map((dx) => (
            <circle key={dx} cx={x + dx} cy={y} r={2.2} />
          ))}
        </g>
      ) : (
        filled && <circle cx={x} cy={y} r={5} fill={colour} />
      )}
    </g>
  );
}

function Caption({
  point,
  width,
  title,
  subtitle,
  offset,
}: {
  point: Point;
  width: number;
  title: string;
  subtitle: string;
  offset: number;
}) {
  return (
    <div
      className="pointer-events-none absolute text-center"
      style={{
        left: point.x - width / 2,
        top: point.y + offset,
        width: width,
      }}
    >
      <p className="mx-auto line-clamp-2 w-fit max-w-full break-words bg-surface px-1 text-[13px] leading-4 font-semibold text-ink">
        {title}
      </p>
      <p className="mx-auto mt-0.5 w-fit max-w-full truncate bg-surface px-1 text-xs text-ink-muted">{subtitle}</p>
    </div>
  );
}

/** What a box says under it: the running attempt, the verdict, the evidence, who decided. */
function detailOf(step: Step, state: StepState, structural = false): string | null {
  const last = state.attempts.at(-1);
  if (state.verdict) return state.verdict === "approve" ? "✓ approved" : state.verdict.replace(/_/g, " ");
  const evidence = (last?.evidence ?? {}) as Record<string, unknown>;
  if (typeof evidence.tests_run === "number") {
    const failures = Number(evidence.tests_failed ?? 0);
    return failures > 0 ? `✗ ${failures} of ${evidence.tests_run} tests fail` : `✓ ${evidence.tests_run} tests pass`;
  }
  if (step.kind === "human") {
    if (state.status === "attend" && last?.due_at) return `due ${relative(last.due_at)}`;
    if (last?.decided_by) return `${last.status} by ${last.decided_by.split("@")[0]}`;
  }
  if (step.kind === "train" && last)
    return last.decided_by ? `approved by ${last.decided_by.split("@")[0]}` : last.status;
  if (step.kind === "platform" && step.gates.length) {
    // A workflow's map has no work item: nothing is met or unmet yet, and empty boxes read as failures.
    if (structural) return `waits for: ${step.gates.map((g) => g.replace(/_/g, " ")).join(", ")}`;
    const tick = state.status === "fait" ? "☑" : "☐";
    return step.gates.map((g) => `${tick} ${g.replace(/_/g, " ")}`).join("  ");
  }
  return null;
}

function roundOf(step: Step, state: StepState): string | null {
  if (state.rounds < 2) return null;
  const sur = step.maxRounds ? ` of ${step.maxRounds}` : "";
  if (state.status === "fait")
    return `${state.verdict === "approve" || step.kind === "human" ? "approved" : "passed"} in round ${state.rounds}`;
  return `round ${state.rounds}${sur}`;
}

function StepButton({
  step,
  state,
  point,
  width,
  chosen,
  onSelect,
  structural = false,
  flow,
}: {
  step: Step;
  state: StepState;
  point: Point;
  width: number;
  chosen: boolean;
  onSelect: () => void;
  structural?: boolean;
  flow?: "produces" | "reads";
}) {
  const detail = detailOf(step, state, structural);
  const outputs = structural ? outputsOf(step) : null;
  const round = roundOf(step, state);
  const kind = {
    agent: "agent",
    human: "person",
    platform: "platform",
    train: "release train",
    decision: "decision",
  }[step.kind];
  // The visible text names the button, so a voice user can say it (axe `label-content-name-mismatch`);
  // what the box only shows by its colour, or not at all, goes in a hidden description.
  const description = [
    kind,
    structural ? null : STATUS_LABEL[state.status],
    `${state.attempts.length} attempt${state.attempts.length === 1 ? "" : "s"}`,
  ]
    .filter(Boolean)
    .join(" — ");
  const descriptionId = useId();
  return (
    <button
      type="button"
      data-testid={`etape-${step.id}`}
      data-statut={state.status}
      data-donnee
      data-flow={flow}
      aria-describedby={descriptionId}
      aria-pressed={chosen}
      title={step.sentence?.replaceAll("`", "") ?? undefined}
      onClick={onSelect}
      className={cn(
        "group absolute flex flex-col items-center rounded text-center focus-visible:outline-2 focus-visible:outline-offset-2",
        flow === "produces" && "outline-2 outline-offset-2 outline-agent-line",
        flow === "reads" && "outline-2 outline-dashed outline-offset-2 outline-line-strong",
      )}
      style={{ left: point.x - (width - 8) / 2, top: point.y - RADIUS - 4, width: width - 8 }}
    >
      <span aria-hidden className="block" style={{ height: 2 * RADIUS + 8 }} />
      <span id={descriptionId} hidden>
        {description}
      </span>
      <span className="mx-auto line-clamp-2 block w-fit max-w-full break-words bg-surface px-1 text-[13px] leading-4 font-semibold text-ink group-hover:underline">
        {step.label}
      </span>
      <span className="mx-auto mt-0.5 line-clamp-2 block w-fit max-w-full break-words bg-surface px-1 text-xs leading-4 text-ink-muted">
        {step.action}
      </span>
      {outputs && (
        <span
          data-testid={`sorties-${step.id}`}
          className="mx-auto mt-0.5 line-clamp-2 block w-fit max-w-full break-words bg-surface px-1 text-[11px] leading-4 text-ink-muted"
        >
          {outputs}
        </span>
      )}
      {(round || detail) && (
        <span className="mx-auto mt-0.5 flex w-fit max-w-full flex-wrap items-center justify-center gap-1 bg-surface px-1 text-xs leading-4">
          {round && (
            <span
              data-testid={`tour-${step.id}`}
              className={cn(
                "whitespace-nowrap rounded-full border px-1.5 text-[11px] font-medium",
                state.status === "fait"
                  ? "border-succeeded-line bg-succeeded-soft text-succeeded-ink"
                  : "border-retrying-line bg-retrying-soft text-retrying-ink",
              )}
            >
              {toSentenceCase(round)}
            </span>
          )}
          {detail && (
            <span
              className={cn(
                "min-w-0 truncate",
                state.status === "echoue" || /fail/.test(detail)
                  ? "text-failed-ink"
                  : state.status === "renvoye" || /changes/.test(detail)
                    ? "text-retrying-ink"
                    : state.status === "fait"
                      ? "text-succeeded-ink"
                      : "text-ink-muted",
              )}
            >
              {detail}
            </span>
          )}
        </span>
      )}
    </button>
  );
}

function Exceptions({ model, instant }: { model: JourneyModel; instant: Instant }) {
  if (model.exceptions.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-muted" data-testid="parcours-exceptions">
      <span>When something goes wrong:</span>
      {model.exceptions.map((exception) => {
        const here = instant.state === exception.id;
        const gone = instant.visited.has(exception.id);
        return (
          <span
            key={exception.id}
            data-statut={here ? "actif" : gone ? "passe" : "a_venir"}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5",
              here
                ? "border-running-line bg-running-soft font-medium text-running-ink"
                : gone
                  ? "border-retrying-line bg-retrying-soft text-retrying-ink"
                  : "border-line text-ink-muted",
            )}
          >
            {here && <span aria-hidden className="parcours-pouls-point inline-block size-1.5 rounded-full bg-current" />}
            {exception.display}
            {here ? " — here now" : gone ? " — went through" : ""}
          </span>
        );
      })}
    </div>
  );
}

function Phases({
  journey,
  model,
  instant,
  frames,
  onGoTo,
}: {
  journey: WorkItemJourney;
  model: JourneyModel;
  instant: Instant;
  frames: number[];
  onGoTo: (index: number) => void;
}) {
  return (
    <nav aria-label="Stages of the journey" className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
      <ol className="flex flex-wrap gap-2">
        {model.phases.map((phase, i) => {
          const statuses = phase.steps.map((id) => instant.steps.get(id)?.status);
          const done = statuses.every((s) => s === "fait");
          const current = instant.phase === i && !done;
          const start = phaseStart(journey, model, phase);
          const index = start === null ? -1 : frames.findIndex((t) => t >= start);
          return (
            <li key={phase.key}>
              <button
                type="button"
                disabled={index < 0}
                aria-current={current ? "step" : undefined}
                onClick={() => onGoTo(index)}
                data-testid={`phase-${i}`}
                className={cn(
                  "inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                  current ? "border-running-line text-ink" : "border-line text-ink-muted hover:text-ink",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "inline-flex size-5 items-center justify-center rounded-full font-mono text-[11px]",
                    done
                      ? "bg-succeeded-soft text-succeeded-ink"
                      : current
                        ? "bg-running-soft text-running-ink"
                        : "bg-surface-sunken text-ink-muted",
                  )}
                >
                  {i + 1}
                </span>
                {phase.label}
                <span className="sr-only">{done ? " (done)" : current ? " (current)" : ""}</span>
              </button>
            </li>
          );
        })}
      </ol>
      <span className="text-xs text-ink-muted">Click a stage to jump to it</span>
    </nav>
  );
}
