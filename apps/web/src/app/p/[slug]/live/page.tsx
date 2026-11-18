// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { Maximize2, Minimize2 } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { use, useCallback, useMemo } from "react";
import { LiveBoard, Wall } from "@/components/live/live-board";
import { Button, Empty, ErrorNote } from "@/components/ui";
import { itemsOf } from "@/components/workflows/board";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useSession } from "@/lib/session";
import { type Currency, monthStart, usdToCurrency } from "@/lib/spend";
import { workflowHueClasses } from "@/lib/workflow-hue";

/**
 * The live view (S67-27, ADR 0187 §4): the workflow's states as full-height lanes, a card per work item,
 * and what is happening on them as it happens. It reads what the board reads (the items, the workflow
 * and its validated graph) and listens to the organisation's activity stream; nothing new is written.
 * `?workflow=` picks the workflow, `?wall=1` takes the page out of the console for a screen on a wall.
 */
export default function LivePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  const { org, orgs } = useSession();
  const currency: Currency =
    orgs.find((o) => o.slug === org)?.currency === "USD" ? "USD" : "EUR";
  const today = new Date().toISOString().slice(0, 10);
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const chosen = search.get("workflow");
  const wall = search.get("wall") === "1";

  const items = useQuery({
    queryKey: ["items", slug],
    queryFn: () => api.allWorkItems(slug),
  });
  const workflows = useQuery({
    queryKey: ["workflows", slug],
    queryFn: () => api.workflows(slug),
  });
  const list = workflows.data ?? [];
  const current = chosen
    ? list.find((w) => w.name === chosen)
    : (list.find((w) => w.is_default) ?? list[0]);
  const name = current?.name ?? "";
  // The same query keys as the board: a definition read there is not read again here.
  const definition = useQuery({
    queryKey: ["workflow", slug, name],
    queryFn: () => api.workflowNamed(slug, name),
    enabled: Boolean(name),
  });
  const validation = useQuery({
    queryKey: ["workflow-validation", slug, name, definition.data?.version],
    queryFn: () => api.validateWorkflow(definition.data?.yaml ?? ""),
    enabled: Boolean(definition.data?.yaml),
  });

  // What the ledger says for this month in this project: today's spend, and the rate to the org's currency.
  const spendReport = useQuery({
    queryKey: ["live-spend", slug, today],
    queryFn: () => api.costs(slug, "day", { since: monthStart(new Date()) }),
    refetchInterval: 60_000,
  });
  const spentToday =
    spendReport.data?.rows.find((r) => r.key === today)?.cost_usd ??
    (spendReport.data ? 0 : null);
  const usdTo = spendReport.data ? usdToCurrency(spendReport.data.total) : null;

  // One array for as long as the read is the same, so that the flow reads each fresh answer once.
  const mine = useMemo(
    () => itemsOf(items.data?.items ?? [], name, Boolean(current?.is_default)),
    [items.data, name, current?.is_default],
  );

  const href = useCallback(
    (next: { workflow?: string | null; wall?: boolean }) => {
      const q = new URLSearchParams();
      const workflow = next.workflow === undefined ? chosen : next.workflow;
      if (workflow) q.set("workflow", workflow);
      if (next.wall) q.set("wall", "1");
      const s = q.toString();
      return s ? `${pathname}?${s}` : pathname;
    },
    [chosen, pathname],
  );
  const exitWall = useCallback(
    () => router.replace(href({ wall: false })),
    [router, href],
  );

  if (items.error)
    return <ErrorNote>{(items.error as Error).message}</ErrorNote>;
  if (workflows.error)
    return <ErrorNote>{(workflows.error as Error).message}</ErrorNote>;
  if (chosen && workflows.data && !current) {
    return (
      <Empty>
        No workflow named <span className="font-mono">{chosen}</span> in this
        project. <Link href={pathname}>See the default one</Link>
      </Empty>
    );
  }
  const ready =
    Boolean(current) &&
    !items.isLoading &&
    !definition.isLoading &&
    !validation.isLoading;
  if (!ready || !current) return <Empty>Reading the workflow…</Empty>;

  // One row, the workflow switcher (the chips only link when there is a choice); LIVE sits at its right.
  const switcher = (
    <nav
      aria-label="Live workflow"
      className="flex flex-wrap items-center gap-1.5"
    >
      {list.map((w) => {
        const hue = workflowHueClasses(w.name);
        const active = w.name === current.name;
        const style = cn(
          "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm no-underline whitespace-nowrap",
          active
            ? cn(hue.pill, "font-medium")
            : "border-line text-ink-muted hover:text-ink",
        );
        const inner = (
          <>
            <span aria-hidden className={cn("size-2 rounded-full", hue.solid)} />
            {w.name}
          </>
        );
        return list.length > 1 ? (
          <Link
            key={w.name}
            href={href({ workflow: w.name })}
            aria-current={active ? "page" : undefined}
            data-verbatim
            className={style}
          >
            {inner}
          </Link>
        ) : (
          <span key={w.name} data-verbatim className={style}>
            {inner}
          </span>
        );
      })}
    </nav>
  );
  const board = (
    <LiveBoard
      // A new workflow or graph starts a new flow.
      key={`${current.name}|${definition.data?.version ?? ""}`}
      graph={validation.data?.graph}
      items={mine}
      org={org}
      slug={slug}
      workflow={current.name}
      wall={wall}
      switcher={wall ? undefined : switcher}
      currency={currency}
      usdTo={usdTo}
      spentTodayUsd={spentToday}
      toolbar={
        wall ? (
          <Button size="sm" onClick={exitWall}>
            <Minimize2 className="size-4" aria-hidden /> Exit wall mode
          </Button>
        ) : (
          <Button
            size="sm"
            onClick={() => router.replace(href({ wall: true }))}
          >
            <Maximize2 className="size-4" aria-hidden /> Wall mode
          </Button>
        )
      }
    />
  );
  if (wall) return <Wall onExit={exitWall}>{board}</Wall>;
  // `data-full-width`: the lanes use the whole window (app/layout.tsx).
  return (
    <div className="space-y-4" data-full-width>
      {board}
    </div>
  );
}
