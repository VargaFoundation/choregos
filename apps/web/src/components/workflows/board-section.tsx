// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { useState } from "react";
import { DecisionBar } from "@/components/decision-bar";
import { ActorIcon, Badge, Card, CostChip, Dot, Heading, LEGENDE, tonDEtat } from "@/components/ui";
import { type Column, foldedItems } from "@/components/workflows/board";
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

/** The id a column carries: unique on a page that shows several workflows. */
export const columnId = (workflow: string, state: string) => `colonne-${workflow}-${state}`;

export function BoardSection({
  slug,
  section,
  alone,
  onDecided,
}: {
  slug: string;
  section: BoardSectionModel;
  /** The only workflow on the page: no link to show it alone. */
  alone: boolean;
  onDecided: () => void;
}) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const headingId = `workflow-${section.name}`;
  const hue = workflowHueClasses(section.name);
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
          <span data-donnee>{section.name}</span>
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
      {section.columns.length === 0 ? (
        <p className="text-sm text-ink-muted">
          No work item in this workflow{section.hidden > 0 ? ` (${section.hidden} empty columns hidden)` : ""}.
        </p>
      ) : (
        <>
          {/* The summary of the columns: each one is named, even when it is out of sight. */}
          <nav aria-label={`Columns of ${section.name}`} data-donnee className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
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
              />
            ))}
          </div>
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
}: {
  slug: string;
  workflow: string;
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
        <Dot tone={tonDEtat(column.state, column.kind)} />
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
        <BoardCard key={item.id} slug={slug} item={item} onDecided={onDecided} />
      ))}
      {folded > 0 && (
        <button type="button" className="text-xs text-accent-strong" onClick={onExpand}>
          Show all {column.items.length}
        </button>
      )}
    </section>
  );
}

function BoardCard({ slug, item, onDecided }: { slug: string; item: WorkItemDto; onDecided: () => void }) {
  return (
    <div id={`carte-${item.id}`} className="scroll-mt-16 target:outline target:outline-2 target:outline-accent">
      <Card className="p-2.5">
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
        {item.current_run && (
          <p className="mt-2 flex items-center gap-2 text-xs">
            <ActorIcon kind="agent" name={`${item.current_run.stage_role} · attempt ${item.current_run.attempt}`} />
            <span className="text-ink-muted">{item.current_run.status}</span>
          </p>
        )}
        {item.failure && (
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
