"use client";

import { Heading } from "@varga/design-system";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";
import { DecisionBar } from "@/components/decision-bar";
import { ActorIcon, Card, CostChip, Empty, ErrorNote, StateBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import type { WorkItemDto } from "@/lib/types";

/** Le board reprend les états du workflow : les colonnes sont celles du DSL, pas les nôtres. */
export default function BoardPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const queryClient = useQueryClient();
  const items = useQuery({ queryKey: ["items", slug], queryFn: () => api.workItems(slug) });
  const workflow = useQuery({ queryKey: ["workflow", slug], queryFn: () => api.workflow(slug) });

  if (items.error) return <ErrorNote>{(items.error as Error).message}</ErrorNote>;

  const columns = columnsFrom(workflow.data?.yaml, items.data?.items ?? []);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <Heading as="h2" size="md">
          board
        </Heading>
        <span className="text-sm text-ink-muted">
          workflow {workflow.data?.name ?? "—"} v{workflow.data?.version ?? "?"}
        </span>
      </div>
      {items.isLoading && <Empty>chargement…</Empty>}
      <div className="grid gap-3 overflow-x-auto md:grid-flow-col md:auto-cols-[minmax(260px,1fr)]">
        {columns.map((column) => (
          <section key={column.state} className="space-y-2">
            <header className="flex items-center justify-between">
              <StateBadge state={column.state} display={column.display} kind={column.kind} />
              <span className="text-xs text-ink-muted">{column.items.length}</span>
            </header>
            {column.items.map((item) => (
              <Card key={item.id} className="p-3">
                <div className="flex items-start justify-between gap-2">
                  <Link href={`/p/${slug}/items/${item.id}`} className="text-sm font-medium no-underline">
                    {item.title}
                  </Link>
                  <CostChip costEur={item.totals?.cost_eur} tokensIn={item.totals?.tokens_in} />
                </div>
                <p className="mt-1 font-mono text-xs text-ink-muted">{item.tracker_key}</p>
                {item.current_run && (
                  <p className="mt-2 flex items-center gap-2 text-xs">
                    <ActorIcon kind="agent" name={`${item.current_run.stage_role} · tentative ${item.current_run.attempt}`} />
                    <span className="text-ink-muted">{item.current_run.status}</span>
                  </p>
                )}
                {item.failure && (
                  <p className="mt-1 text-xs font-medium text-danger" title={item.failure.message}>
                    mort · {item.failure.activity ?? "interpréteur"}
                  </p>
                )}
                {item.pending_request && (
                  <div className="mt-2 space-y-2 border border-line border-l-2 border-l-warn bg-surface p-2">
                    <p className="text-xs text-warn">
                      {String(item.pending_request.payload?.summary ?? item.pending_request.kind)} ·{" "}
                      {relative(item.pending_request.requested_at)}
                    </p>
                    <DecisionBar
                      itemId={item.id}
                      kind={item.pending_request.kind}
                      onDone={() => queryClient.invalidateQueries({ queryKey: ["items", slug] })}
                    />
                  </div>
                )}
              </Card>
            ))}
          </section>
        ))}
      </div>
    </div>
  );
}

interface Column {
  state: string;
  display: string;
  /** Absent quand le workflow ne le dit pas : le badge le déduit alors du nom de l'état. */
  kind?: string;
  items: WorkItemDto[];
}

/** Colonnes = états déclarés dans le workflow, dans l'ordre du YAML ; le reste suit. */
function columnsFrom(yaml: string | undefined, items: WorkItemDto[]): Column[] {
  const declared: Array<{ state: string; display: string; kind?: string }> = [];
  if (yaml) {
    const lines = yaml.split("\n");
    let inStates = false;
    for (const line of lines) {
      if (/^states:/.test(line)) {
        inStates = true;
        continue;
      }
      if (inStates && /^\S/.test(line)) break;
      const match = inStates ? /^\s{2}([a-z0-9_-]+):\s*\{?(.*)$/.exec(line) : null;
      if (match) {
        const [, state, rest] = match;
        const display = /display:\s*([^,}]+)/.exec(rest ?? "")?.[1]?.trim() ?? state ?? "";
        // Ce que le YAML dit explicitement ; sinon rien, et le badge déduit le genre du nom de
        // l'état plutôt que de peindre tout en « travail d'agent ».
        const kind = /terminal:\s*true/.test(rest ?? "")
          ? "terminal"
          : /kind:\s*wait/.test(rest ?? "")
            ? "wait"
            : undefined;
        declared.push({ state: state ?? "", display, kind });
      }
    }
  }
  const byState = new Map<string, WorkItemDto[]>();
  for (const item of items) {
    byState.set(item.state, [...(byState.get(item.state) ?? []), item]);
  }
  const columns: Column[] = declared.map((entry) => ({
    ...entry,
    items: byState.get(entry.state) ?? [],
  }));
  for (const [state, stateItems] of byState) {
    if (!columns.some((column) => column.state === state)) {
      columns.push({ state, display: stateItems[0]?.state_display ?? state, items: stateItems });
    }
  }
  return columns.filter((column) => column.items.length > 0 || declared.length <= 12);
}
