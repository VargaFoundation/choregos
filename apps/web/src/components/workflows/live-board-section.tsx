// SPDX-License-Identifier: Apache-2.0
"use client";

import { useMemo } from "react";
import { type Feed, liveMoney, useLiveFlow } from "@/components/live/live-kit";
import { columnsFromGraph, itemsOf, visibleColumns } from "@/components/workflows/board";
import { BoardSection, type BoardSectionModel } from "@/components/workflows/board-section";
import type { Currency } from "@/lib/spend";
import type { WorkflowGraph, WorkItemDto } from "@/lib/types";

/**
 * A workflow's section of the board, kept live (ADR 0187 §4, S67-27): the page's activity feed drives
 * the reduction; a card is in the column its work item says, and a run in flight, a failure or a wait
 * is read off the flow. The tickets of the workflow are the stream's re-read items (or the demo's script).
 */
export function LiveBoardSection({
  slug,
  model,
  graph,
  items,
  isDefault,
  showEmpty,
  feed,
  demo,
  alone,
  onDecided,
  currency,
  usdTo,
  spentTodayUsd,
}: {
  slug: string;
  /** The section as the page reads it: its heading, version and description. */
  model: BoardSectionModel;
  /** Absent until the validator has read the workflow. */
  graph: WorkflowGraph | undefined;
  /** Every ticket of the project: the same array for as long as the read is the same. */
  items: WorkItemDto[];
  isDefault: boolean;
  showEmpty: boolean;
  feed: Feed;
  demo: boolean;
  alone: boolean;
  onDecided: () => void;
  currency: Currency;
  usdTo: number | null;
  spentTodayUsd: number | null;
}) {
  const mine = useMemo(() => itemsOf(items, model.name, isDefault), [items, model.name, isDefault]);
  const live = useLiveFlow({ graph, items: mine, workflow: model.name, feed, demo });
  const all = columnsFromGraph(graph, live.items);
  const { shown, hidden } = visibleColumns(all, showEmpty);
  const tickets = all.flatMap((column) => column.items);
  const format = (usd: number | null | undefined) => liveMoney(usd, currency, usdTo);
  return (
    <BoardSection
      slug={slug}
      section={{
        ...model,
        columns: shown,
        hidden,
        total: tickets.length,
        waiting: tickets.filter((item) => item.pending_request).length,
      }}
      alone={alone}
      onDecided={onDecided}
      live={{
        flow: live.flow,
        arrived: live.arrived,
        format,
        // The ledger's figure is the project's: it is this workflow's only when it is the only one.
        spent: alone ? format(spentTodayUsd) : null,
      }}
    />
  );
}
