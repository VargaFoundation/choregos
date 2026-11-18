// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { DecisionBar } from "@/components/decision-bar";
import { ActorIcon, Badge, Card, CostChip, Dot, Heading, LEGENDE, toneOfState } from "@/components/ui";
import { Summary, Ticker, useFlip, useReducedMotion, stageLine } from "@/components/live/live-kit";
import { type Column, foldedItems } from "@/components/workflows/board";
import type { LiveCard, LiveView } from "@/lib/live-flow";
import { formatElapsed } from "@/lib/activity";
import { cn } from "@/lib/cn";
import { workflowHueClasses } from "@/lib/workflow-hue";
import { relative } from "@/lib/format";
import type { WorkItemDto } from "@/lib/types";

/** One workflow on the board: its heading, a summary of its columns, and the columns themselves. */
export interface BoardSectionModel {
  name: string;
  version: number | null;
  description?: string | null;
  isDefault: boolean;
  /** The columns shown (empty ones only when the viewer asked for them). */
  columns: Column[];
  /** Empty columns left out. */
  hidden: number;
  /** Every ticket of the workflow, whatever the shown columns. */
  total: number;
  waiting: number;
}

/** What the live flow knows of a workflow (ADR 0187 §4): the reduction's view, and how to write what it spent. */
export interface BoardLive {
  flow: LiveView;
  /** Cards that have just arrived in their column. */
  arrived: Set<string>;
  format: (usd: number | null | undefined) => string;
  /** What the project spent today, written; `null` when it is not this workflow's alone to tell. */
  spent: string | null;
}

/** The id a column carries: unique on a page that shows several workflows. */
export const columnId = (workflow: string, state: string) => `colonne-${workflow}-${state}`;

export function BoardSection({
  slug,
  section,
  alone,
  onDecided,
  live,
}: {
  slug: string;
  section: BoardSectionModel;
  /** Present when the board is fed by the activity stream (or the demo's script). */
  live?: BoardLive;
  /** The only workflow on the page: no link to show it alone. */
  alone: boolean;
  onDecided: () => void;
}) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const headingId = `workflow-${section.name}`;
  const hue = workflowHueClasses(section.name);
  const reduced = useReducedMotion();
  const columnsRef = useRef<HTMLDivElement>(null);
  // A card that changes column slides there (FLIP); the signature changes when one does.
  useFlip(
    columnsRef,
    section.columns.map((column) => `${column.state}:${column.items.map((item) => item.id).join(",")}`).join("|"),
    Boolean(live) && !reduced,
  );
  const cards = new Map((live?.flow.cards ?? []).map((card) => [card.id, card]));
  return (
    // The workflow's colour on its section's edge (ADR 0120): the same as its pill above and its chip
    // on a ticket, so a column is never read as another workflow's.
    <section
      aria-labelledby={headingId}
      data-workflow={section.name}
      className={cn("space-y-2 rounded-md border border-line border-t-4 p-3", hue.stripe)}
    >
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span aria-hidden className={cn("size-2.5 shrink-0 rounded-full", hue.solid)} />
        <Heading as="h2" size="md" id={headingId} className={hue.ink}>
          <span data-verbatim>{section.name}</span>
        </Heading>
        <span className="font-mono text-xs text-ink-muted">v{section.version ?? "?"}</span>
        {section.isDefault && <Badge tone="neutral">Default</Badge>}
        <span className="text-xs text-ink-muted" data-testid={`board-count-${section.name}`}>
          {section.total} ticket{section.total === 1 ? "" : "s"}
          {section.waiting > 0 && <span className="font-medium text-waiting-ink"> · {section.waiting} waiting</span>}
        </span>
        {!alone && (
          <Link href={`/p/${slug}/board?workflow=${encodeURIComponent(section.name)}`} className="ml-auto text-xs">
            Only this workflow
          </Link>
        )}
      </header>
      {section.description && <p className="text-xs text-ink-muted">{section.description}</p>}
      {live && <Summary flow={live.flow} spent={live.spent} wall={false} />}
      {section.columns.length === 0 ? (
        <p className="text-sm text-ink-muted">
          No work item in this workflow{section.hidden > 0 ? ` (${section.hidden} empty columns hidden)` : ""}.
        </p>
      ) : (
        <>
          {/* The summary of the columns: each one is named, even when it is out of sight. */}
          <nav aria-label={`Columns of ${section.name}`} data-verbatim className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            {section.columns.map((column) => {
              const waiting = column.items.filter((item) => item.pending_request).length;
              return (
                <a key={column.state} href={`#${columnId(section.name, column.state)}`} className="text-ink-muted hover:text-ink">
                  {column.display} <span className="tabular-nums">{column.items.length}</span>
                  {waiting > 0 && <span className="font-medium text-waiting-ink"> · {waiting} waiting</span>}
                </a>
              );
            })}
          </nav>
          {/* Columns share the width: a column with tickets grows, an empty one stays a narrow rail.
              What still does not fit scrolls inside the section, never under the page's edge. */}
          <div
            ref={columnsRef}
            className="flex flex-col gap-2 pb-2 md:flex-row md:overflow-x-auto"
            data-testid={`board-columns-${section.name}`}
          >
            {section.columns.map((column) => (
              <BoardColumn
                key={column.state}
                slug={slug}
                workflow={section.name}
                column={column}
                expanded={expanded.has(column.state)}
                onExpand={() => setExpanded(new Set([...expanded, column.state]))}
                onDecided={onDecided}
                cards={cards}
                live={live}
              />
            ))}
          </div>
          {live && (
            // The latest events, out of the way until asked for.
            <details className="text-xs" data-testid="board-ticker-details">
              <summary className="cursor-pointer text-ink-muted">Latest events</summary>
              <div className="mt-2">
                <Ticker entries={live.flow.ticker} lanes={live.flow.lanes} wall={false} />
              </div>
            </details>
          )}
        </>
      )}
    </section>
  );
}

function BoardColumn({
  slug,
  workflow,
  column,
  expanded,
  onExpand,
  onDecided,
  cards,
  live,
}: {
  slug: string;
  workflow: string;
  cards: Map<string, LiveCard>;
  live?: BoardLive;
  column: Column;
  expanded: boolean;
  onExpand: () => void;
  onDecided: () => void;
}) {
  const { items, folded } = foldedItems(column, expanded);
  const empty = column.items.length === 0;
  return (
    <section
      id={columnId(workflow, column.state)}
      aria-label={`${column.display}, ${column.items.length} ticket${column.items.length > 1 ? "s" : ""}`}
      className={cn(
        "scroll-mt-16 space-y-2",
        empty ? "md:w-28 md:flex-none" : "md:min-w-48 md:max-w-80 md:flex-1 md:basis-48",
      )}
    >
      <header className="flex h-8 items-center gap-2 border-b border-line" title={column.state}>
        <Dot tone={toneOfState(column.state, column.kind)} />
        <h3 className={cn(LEGENDE, "truncate")}>{column.display}</h3>
        <span className="ml-auto rounded-full bg-surface-muted px-1.5 font-mono text-[11px] tabular-nums text-ink-muted">
          {column.items.length}
        </span>
      </header>
      {column.horsWorkflow && (
        // Without these words, an "In progress" column after "Done" made no sense (audit of 08/10).
        <p className="text-xs text-ink-muted">Not a state of this workflow — tickets left here by an older version</p>
      )}
      {items.map((item) => (
        <BoardCard
          key={item.id}
          slug={slug}
          item={item}
          onDecided={onDecided}
          card={cards.get(item.id)}
          live={live}
        />
      ))}
      {folded > 0 && (
        <button type="button" className="text-xs text-accent-strong" onClick={onExpand}>
          Show all {column.items.length}
        </button>
      )}
    </section>
  );
}

function BoardCard({
  slug,
  item,
  onDecided,
  card,
  live,
}: {
  slug: string;
  item: WorkItemDto;
  onDecided: () => void;
  card?: LiveCard;
  live?: BoardLive;
}) {
  const running = card?.running ?? null;
  const failure = card?.failure ?? null;
  const status = card?.status ?? "idle";
  const arrived = live?.arrived.has(item.id) ?? false;
  const note = failure?.summary ? `${item.id}-why` : undefined;
  return (
    <div
      id={`carte-${item.id}`}
      data-card-id={item.id}
      data-status={live ? status : undefined}
      data-arrived={arrived ? "true" : undefined}
      className={cn(
        "group relative scroll-mt-16 target:outline target:outline-2 target:outline-accent",
        arrived && "live-arrived",
      )}
    >
      <Card
        className={cn(
          "p-2.5",
          status === "running" && "live-running relative overflow-hidden border-running-line",
          status === "failed" && "border-failed-line bg-failed-soft",
          status === "waiting" && "border-waiting-line bg-waiting-soft",
        )}
      >
        <div className="flex items-start justify-between gap-2">
          {item.redacted ? (
            // Its runs read a restricted knowledge base you do not read (ADR 0114): listed, not opened.
            <span className="text-sm font-medium">{item.title}</span>
          ) : (
            <Link href={`/p/${slug}/items/${item.id}`} className="text-sm font-medium no-underline">
              {item.title}
            </Link>
          )}
          {item.redacted ? (
            <Badge tone="neutral" title={`Read by the readers of ${(item.restricted_knowledge ?? []).join(", ")}`}>
              Restricted
            </Badge>
          ) : (
            <CostChip costUsd={item.totals?.cost_usd} tokensIn={item.totals?.tokens_in} />
          )}
        </div>
        <p className="mt-1 font-mono text-xs text-ink-muted">{item.tracker_key}</p>
        {running && live ? (
          // What is running now: the stage (and the agent when it is not the stage's own name), for how
          // long, and what it cost so far (ADR 0187 §4).
          <p className="mt-2 flex flex-wrap items-center gap-x-1.5 text-xs text-running-ink">
            <span data-testid="stage-line">{stageLine(running.stage, running.agent)}</span>
            <span className="tabular-nums">· {formatElapsed(running.elapsed_ms)}</span>
            <span className="tabular-nums">· {live.format(running.cost_usd)}</span>
          </p>
        ) : (
          item.current_run && (
          <p className="mt-2 flex items-center gap-2 text-xs">
            <ActorIcon kind="agent" name={`${item.current_run.stage_role} · attempt ${item.current_run.attempt}`} />
            <span className="text-ink-muted">{item.current_run.status}</span>
          </p>
          )
        )}
        {running && live && <span aria-hidden className="live-progress" />}
        {failure && (
          // A failed run: the reason in red on the card, the summary one hover or focus away.
          <p
            data-testid="failure-reason"
            className="mt-1 text-xs font-medium text-failed-ink"
            // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
            tabIndex={0}
            aria-describedby={note}
          >
            {failure.reason.replace(/_/g, " ")}
          </p>
        )}
        {failure?.summary && (
          <p
            id={note}
            role="tooltip"
            className="absolute inset-x-2 top-full z-10 mt-1 hidden rounded-md border border-failed-line bg-surface p-2 text-xs text-ink shadow-lg group-focus-within:block group-hover:block"
          >
            {failure.summary}
          </p>
        )}
        {item.failure && !failure && (
          <p className="mt-1 text-xs font-medium text-failed-ink" title={item.failure.message}>
            Dead · {item.failure.activity ?? "interpreter"}
          </p>
        )}
        {item.pending_request && (
          <div className="mt-2 space-y-2 border border-line border-l-2 border-l-waiting bg-surface p-2">
            <p className="text-xs text-waiting-ink">
              {String(item.pending_request.payload?.summary ?? item.pending_request.kind)} ·{" "}
              {relative(item.pending_request.requested_at)}
            </p>
            {item.pending_request.kind === "task" ? (
              // A form and an attestation do not fit in a card: the work item's page.
              <Link href={`/p/${slug}/items/${item.id}`} className="text-xs">
                Do the task
              </Link>
            ) : (
              <DecisionBar itemId={item.id} kind={item.pending_request.kind} onDone={onDecided} />
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
