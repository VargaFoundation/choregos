// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { use, useState } from "react";
import { Button, Drawer, Empty, ErrorNote } from "@/components/ui";
import { WorkflowChip } from "@/components/workflow-chip";
import { cn } from "@/lib/cn";
import { workflowHueClasses } from "@/lib/workflow-hue";
import { boardWorkflows, columnsFromGraph, itemsOf, visibleColumns } from "@/components/workflows/board";
import { BoardSection, type BoardSectionModel } from "@/components/workflows/board-section";
import { NewWorkItemForm } from "@/components/workflows/new-work-item";
import { useShowEmptyColumns } from "@/components/workflows/show-empty-columns";
import { api, WORK_ITEMS_CAP } from "@/lib/api";
import { trackerOf } from "@/lib/tracker";
import type { WorkItemDto } from "@/lib/types";

/**
 * The project's board (ADR 0031, S16-10): one section per workflow, the default first, each with the
 * workflow's states as columns (read by the validator) and the tickets pinned to it. A project with
 * several workflows showed only the default one, and its other tickets looked lost (FON-270,
 * 2026-11-02). `?workflow=` narrows the page to one workflow and keeps the choice in the URL.
 * The page takes the whole width, and empty columns stay out of the way unless asked for.
 */
export default function BoardPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const queryClient = useQueryClient();
  const pathname = usePathname();
  const search = useSearchParams();
  const items = useQuery({ queryKey: ["items", slug], queryFn: () => api.allWorkItems(slug) });
  const workflows = useQuery({ queryKey: ["workflows", slug], queryFn: () => api.workflows(slug) });
  const defaultWorkflow = workflows.data?.find((w) => w.is_default)?.name ?? workflows.data?.[0]?.name ?? "";
  const chosen = search.get("workflow");
  const shown = boardWorkflows(workflows.data ?? [], chosen);
  // The same query keys as `useWorkflow`: a definition read here is not read again on its pages.
  const definitions = useQueries({
    queries: shown.map((w) => ({
      queryKey: ["workflow", slug, w.name],
      queryFn: () => api.workflowNamed(slug, w.name),
    })),
  });
  const validations = useQueries({
    queries: shown.map((w, index) => {
      const definition = definitions[index]?.data;
      return {
        queryKey: ["workflow-validation", slug, w.name, definition?.version],
        queryFn: () => api.validateWorkflow(definition?.yaml ?? ""),
        enabled: Boolean(definition?.yaml),
      };
    }),
  });
  const [showEmpty, setShowEmpty] = useShowEmptyColumns();
  const connectors = useQuery({ queryKey: ["connectors", slug], queryFn: () => api.connectors(slug) });
  // A work item is filed from here whatever the tracker: with an external one the platform creates
  // the issue there first (ADR 0059).
  const tracker = trackerOf(connectors.data);
  const [nouvelle, setNouvelle] = useState(false);
  const [created, setCreated] = useState<WorkItemDto | null>(null);

  if (items.error) return <ErrorNote>{(items.error as Error).message}</ErrorNote>;

  const sections: BoardSectionModel[] = shown.map((w, index) => {
    const all = columnsFromGraph(validations[index]?.data?.graph, itemsOf(items.data?.items ?? [], w.name, w.name === defaultWorkflow));
    const { shown: columns, hidden } = visibleColumns(all, showEmpty);
    const tickets = all.flatMap((column) => column.items);
    return {
      name: w.name,
      version: definitions[index]?.data?.version ?? w.version,
      description: w.description,
      isDefault: w.is_default,
      columns,
      hidden,
      total: tickets.length,
      waiting: tickets.filter((item) => item.pending_request).length,
    };
  });
  const hiddenColumns = sections.reduce((sum, section) => sum + section.hidden, 0);
  // What waits for a person, and where: a column can still be out of sight, and the only card with an
  // "approve" button used to sit there without a hint (second pass of 08/10, S23-09).
  const waiting = sections.flatMap((section) =>
    section.columns.flatMap((column) =>
      column.items.filter((item) => item.pending_request).map((item) => ({ item, column, workflow: section.name })),
    ),
  );
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["items", slug] });

  return (
    // `data-full-width`: the main container lets this page use the whole window (app/layout.tsx).
    <div className="space-y-4" data-full-width>
      <div className="flex flex-wrap items-center gap-3">
        {workflows.data && workflows.data.length > 0 && (
          <WorkflowSwitcher
            pathname={pathname}
            chosen={chosen}
            workflows={[...workflows.data].sort((a, b) => Number(b.is_default) - Number(a.is_default))}
          />
        )}
        {(hiddenColumns > 0 || showEmpty) && (
          <Button size="sm" aria-pressed={showEmpty} onClick={() => setShowEmpty(!showEmpty)}>
            {showEmpty ? "Hide empty columns" : `Show empty columns (${hiddenColumns})`}
          </Button>
        )}
        <span className="ml-auto">
          <Button tone="accent" size="sm" onClick={() => setNouvelle(true)}>
            New work item
          </Button>
        </span>
      </div>
      <Drawer open={nouvelle} onClose={() => setNouvelle(false)} title="New work item">
        {nouvelle && (
          <NewWorkItemForm
            slug={slug}
            workflows={workflows.data?.map((w) => w.name) ?? []}
            initial={chosen ?? defaultWorkflow}
            tracker={tracker}
            onDone={(item) => {
              setNouvelle(false);
              setCreated(item);
              void refresh();
            }}
          />
        )}
      </Drawer>
      {created && (
        <p role="status" className="text-sm" data-testid="work-item-created">
          Created <span className="font-mono">{created.tracker_key}</span>
          {" · "}
          <Link href={`/p/${slug}/items/${created.id}`}>open it here</Link>
          {created.url && (
            <>
              {" · "}
              <a href={created.url} target="_blank" rel="noreferrer">
                open the issue
              </a>
            </>
          )}
        </p>
      )}
      {items.isLoading && <Empty>Loading…</Empty>}
      {items.data?.meta.has_more && (
        // Never a silent cut (S55-03): the board read the most recent tickets and says so.
        <p role="status" className="border border-info-line bg-info-soft px-3 py-2 text-sm text-info" data-testid="board-truncated">
          The board shows the {items.data.items.length.toLocaleString("en")} most recent tickets of this project (at most{" "}
          {WORK_ITEMS_CAP.toLocaleString("en")}); older ones are not on it.
        </p>
      )}
      {waiting.length > 0 && (
        <div className="raised border border-line border-l-2 border-l-waiting p-3 text-sm" data-testid="attentes-du-board">
          <p className="font-medium">
            {waiting.length} ticket{waiting.length > 1 ? "s" : ""} wait{waiting.length > 1 ? "" : "s"} for a person
          </p>
          <ul className="mt-1 space-y-1">
            {waiting.map(({ item, column, workflow }) => (
              <li key={item.id}>
                <a href={`#carte-${item.id}`}>{item.title}</a>
                <span className="text-ink-muted"> · in {column.display}</span>
                {sections.length > 1 && <WorkflowChip name={workflow} className="ml-2 align-middle" />}
              </li>
            ))}
          </ul>
        </div>
      )}
      {chosen && workflows.data && sections.length === 0 && (
        <Empty>
          No workflow named <span className="font-mono">{chosen}</span> in this project.{" "}
          <Link href={pathname}>See all workflows</Link>
        </Empty>
      )}
      {sections.map((section) => (
        <BoardSection key={section.name} slug={slug} section={section} alone={sections.length === 1} onDecided={refresh} />
      ))}
    </div>
  );
}

/**
 * Which workflow the board shows (S55-03): every workflow of the project as a pill in its own colour,
 * always visible — even alone, the board says which workflow it is about. A link per workflow keeps
 * the choice in the URL (`?workflow=`).
 */
function WorkflowSwitcher({
  pathname,
  chosen,
  workflows,
}: {
  pathname: string;
  chosen: string | null;
  workflows: { name: string; is_default: boolean }[];
}) {
  const several = workflows.length > 1;
  const pill = "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm no-underline whitespace-nowrap";
  return (
    <nav aria-label="Board workflow" className="flex flex-wrap items-center gap-1.5">
      {several && (
        <Link
          href={pathname}
          aria-current={!chosen ? "page" : undefined}
          className={cn(
            pill,
            !chosen ? "border-line-strong bg-surface-muted font-medium text-ink" : "border-line text-ink-muted hover:text-ink",
          )}
        >
          All workflows
        </Link>
      )}
      {workflows.map((workflow) => {
        const hue = workflowHueClasses(workflow.name);
        const active = chosen === workflow.name || (!several && !chosen);
        return (
          <Link
            key={workflow.name}
            href={`${pathname}?workflow=${encodeURIComponent(workflow.name)}`}
            aria-current={active ? "page" : undefined}
            data-donnee
            className={cn(pill, active ? cn(hue.pill, "font-medium") : "border-line text-ink-muted hover:text-ink")}
          >
            <span aria-hidden className={cn("size-2 rounded-full", hue.solid)} />
            <span>{workflow.name}</span>
            {workflow.is_default && <span className="text-xs">(default)</span>}
          </Link>
        );
      })}
    </nav>
  );
}
