// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Clock,
  LiveIndicator,
  Summary,
  Ticker,
  Wall,
  liveMoney,
  useActivityFeed,
  useLiveFlow,
} from "@/components/live/live-kit";
import { Button, Empty, ErrorNote } from "@/components/ui";
import { WorkflowChip } from "@/components/workflow-chip";
import { itemsOf } from "@/components/workflows/board";
import { useWorkflow } from "@/components/workflows/use-workflow";
import { api, IS_MOCK } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useSession } from "@/lib/session";
import { type Currency, monthStart, usdToCurrency } from "@/lib/spend";
import type { WorkflowGraph } from "@/lib/types";
import { FlowView } from "./flow-view";

/** The demo draws its own, richer workflow (`mocks/live.ts`): the mock API's has too few states to travel through. */
function useDemoGraph(): { graph: WorkflowGraph; initial: string } | null {
  const [demo, setDemo] = useState<{ graph: WorkflowGraph; initial: string } | null>(null);
  useEffect(() => {
    if (!IS_MOCK) return;
    let stopped = false;
    void import("@/mocks/live").then(({ demoFlowGraph }) => {
      if (!stopped) setDemo({ graph: demoFlowGraph as unknown as WorkflowGraph, initial: "triage" });
    });
    return () => {
      stopped = true;
    };
  }, []);
  return demo;
}

/**
 * A workflow's flow, live (ADR 0187 §4, S67-29): the workflow drawn as a line of stations with the
 * work items travelling through it, a summary, a ticker, and the wall mode (the same, on the whole
 * screen, for a display nobody operates). A click on a station narrows the ticker to it and lists its
 * tickets; a click on a ticket opens it.
 */
export function FlowScreen({
  slug,
  workflow,
  isDefault,
  wallRequested,
}: {
  slug: string;
  workflow: string;
  isDefault: boolean;
  /** `?wall=1`: the page opens already in wall mode. */
  wallRequested: boolean;
}) {
  const { definition, validation } = useWorkflow(slug, workflow);
  const items = useQuery({ queryKey: ["items", slug], queryFn: () => api.allWorkItems(slug) });
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  const { org, orgs } = useSession();
  const currency: Currency = orgs.find((o) => o.slug === org)?.currency === "USD" ? "USD" : "EUR";
  const today = new Date().toISOString().slice(0, 10);
  const spendReport = useQuery({
    queryKey: ["live-spend", slug, today],
    queryFn: () => api.costs(slug, "day", { since: monthStart(new Date()) }),
    refetchInterval: 60_000,
  });
  const spent = spendReport.data?.rows.find((r) => r.key === today)?.cost_usd ?? (spendReport.data ? 0 : null);
  const usdTo = spendReport.data ? usdToCurrency(spendReport.data.total) : null;
  const format = (usd: number | null | undefined) => liveMoney(usd, currency, usdTo);

  const demo = useDemoGraph();
  const read = validation.data?.graph as WorkflowGraph | undefined;
  const graph = IS_MOCK ? demo?.graph : read;
  const initial = IS_MOCK ? demo?.initial : (definition.data?.json as { initial?: string } | undefined)?.initial;
  const feed = useActivityFeed({ org, slug, demo: IS_MOCK });
  const mine = useMemo(() => itemsOf(items.data?.items ?? [], workflow, isDefault), [items.data, workflow, isDefault]);
  const live = useLiveFlow({ graph, items: mine, workflow, feed, demo: IS_MOCK && Boolean(demo), story: "flow" });

  const [selected, setSelected] = useState<string | null>(null);
  const [wall, setWall] = useState(wallRequested);
  const exit = useCallback(() => setWall(false), []);

  const lane = live.flow.lanes.find((l) => l.state === selected);
  const cards = new Map(live.flow.cards.map((c) => [c.id, c]));
  // What happened at a station: the moves into and out of it, and what its tickets there are doing.
  const here = new Set((lane?.cards ?? []).map((id) => cards.get(id)?.key));
  const ticker = selected
    ? live.flow.ticker.filter((e) => e.from === selected || e.to === selected || here.has(e.key))
    : live.flow.ticker;
  const title = `${project.data?.name ?? slug} · ${workflow}`;

  if (!graph) {
    if (validation.isError || definition.isError) {
      return <ErrorNote>{String(validation.error ?? definition.error)}</ErrorNote>;
    }
    return <p className="text-sm text-ink-muted">Reading the workflow.</p>;
  }
  if (graph.nodes.length === 0) return <Empty>This workflow has no states to draw.</Empty>;

  const flowView = (big: boolean) => (
    <FlowView
      graph={graph}
      initial={initial}
      flow={live.flow}
      workflow={workflow}
      slug={slug}
      now={live.now}
      wall={big}
      arrived={live.arrived}
      selected={selected}
      onSelect={setSelected}
      format={format}
    />
  );
  const summary = (big: boolean) => (
    <Summary flow={live.flow} spent={spent === null ? null : format(spent)} wall={big} />
  );

  return (
    <div className="space-y-4" data-testid="flow-screen">
      <div className="flex flex-wrap items-center gap-3">
        <LiveIndicator connection={feed.connection} wall={false} />
        <span className="ml-auto">
          <Button onClick={() => setWall(true)}>Wall mode</Button>
        </span>
      </div>
      {summary(false)}
      {!wall && (
        <div className="rounded-lg border border-line bg-surface-sunken/40 p-3">{flowView(false)}</div>
      )}
      {selected && lane && (
        <section
          aria-label={`${lane.display}, tickets`}
          data-testid="flow-panel"
          className="rounded-md border border-line bg-surface p-3 text-sm"
        >
          <div className="flex items-center gap-2">
            <h3 className="font-medium">{lane.display}</h3>
            <span className="text-ink-muted">
              {lane.count} {lane.count === 1 ? "item" : "items"}
            </span>
            <button
              type="button"
              aria-label="Clear the station filter"
              onClick={() => setSelected(null)}
              className="ml-auto rounded p-1 text-ink-muted hover:text-ink"
            >
              <X aria-hidden className="size-4" />
            </button>
          </div>
          <ul className="mt-2 flex flex-wrap gap-2">
            {lane.cards.map((id) => {
              const card = cards.get(id);
              return card ? (
                <li key={id}>
                  <Link
                    href={`/p/${slug}/items/${id}`}
                    className="rounded-full border border-line px-2.5 py-0.5 font-mono text-xs no-underline hover:bg-surface-muted"
                  >
                    {card.key} <span className="font-sans text-ink-muted">{card.title}</span>
                  </Link>
                </li>
              ) : null;
            })}
            {lane.cards.length === 0 && <li className="text-ink-muted">Nothing here now.</li>}
          </ul>
        </section>
      )}
      <Ticker entries={ticker} lanes={live.flow.lanes} wall={false} />
      {wall && (
        <Wall onExit={exit}>
          <div className="flex h-full flex-col gap-5 p-8" data-testid="flow-wall">
            <header className="flex items-center gap-6">
              <h1 className="min-w-0 flex-1 truncate text-4xl font-semibold" data-verbatim>
                <span className="text-ink-muted">{project.data?.name ?? slug}</span>
                <span aria-hidden className="mx-3 text-ink-subtle">
                  ·
                </span>
                <WorkflowChip name={workflow} size="md" />
              </h1>
              <LiveIndicator connection={feed.connection} wall />
              <Clock now={live.now} />
              <button
                type="button"
                aria-label="Leave wall mode"
                onClick={exit}
                className={cn("wall-exit rounded-full border border-line p-2 text-ink-muted hover:text-ink")}
              >
                <X aria-hidden className="size-6" />
              </button>
            </header>
            <span className="sr-only">{title}</span>
            {summary(true)}
            <div className="min-h-0 flex-1">{flowView(true)}</div>
            <Ticker entries={ticker} lanes={live.flow.lanes} wall />
          </div>
        </Wall>
      )}
    </div>
  );
}
