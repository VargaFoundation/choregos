// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { Coins } from "lucide-react";
import { useState } from "react";
import { Card, Empty, ReadState, Stat, buttonClasses } from "@/components/ui";
import type { CostFilter } from "@/lib/api";
import { useSession } from "@/lib/session";
import { type Currency, type Measure, money, tokenCount, tokenTotals } from "@/lib/spend";
import { BreakdownTable, csvUrl, fetchReport, reportKey, type Scope } from "./breakdown";

/** The groupings a tokens-and-cost panel may offer, as the cost report names them (ADR 0141 §1). */
export type PanelGroup = "model" | "stage" | "agent" | "workflow" | "run";

const GROUP_LABELS: Record<PanelGroup, string> = {
  model: "By model",
  stage: "By stage",
  agent: "By agent",
  workflow: "By workflow",
  run: "By run",
};

const MEASURES: Array<{ id: Measure; label: string }> = [
  { id: "cost", label: "Cost" },
  { id: "tokens", label: "Tokens" },
];

/** The organisation's currency, as the session knows it; euros until it says otherwise. */
export function useOrgCurrency(): { org: string; currency: Currency } {
  const session = useSession();
  const currency = session.orgs?.find((o) => o.slug === session.org)?.currency === "USD" ? "USD" : "EUR";
  return { org: session.org, currency };
}

/**
 * Tokens and cost (S64-07, ADR 0141 §1): the same cost report every spend screen reads, narrowed to
 * what the page is about — a project, one of its workflows, an agent — over the last `days` days.
 * Its totals say the input, output and cached tokens next to the money; one grouping at a time,
 * in money or in tokens; the CSV is exactly the grouping and the filter shown.
 */
export function TokensAndCostPanel({
  project = null,
  filter = {},
  groups,
  days = 30,
}: {
  /** A project's slug, or `null` for the organisation's report. */
  project?: string | null;
  /** The page's own filter: `{ workflow }`, `{ agent }`, nothing for a project. */
  filter?: Pick<CostFilter, "agent" | "workflow" | "work_item">;
  groups: PanelGroup[];
  days?: number;
}) {
  const { org, currency } = useOrgCurrency();
  const scope: Scope = { org, project };
  // Fixed once per mount: the period does not slide under the reader's eyes.
  const [since] = useState(() => new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10));
  const [group, setGroup] = useState<PanelGroup>(groups[0] ?? "model");
  const [measure, setMeasure] = useState<Measure>("cost");
  const filtre: CostFilter = { since, ...filter };
  const report = useQuery({ queryKey: reportKey(scope, group, filtre), queryFn: () => fetchReport(scope, group, filtre) });
  const totals = tokenTotals(report.data?.total);
  const cost = currency === "USD" ? report.data?.total.cost_usd : report.data?.total.cost_eur;
  return (
    <Card
      eyebrow={`Last ${days} days`}
      title="Tokens and cost"
      icon={Coins}
      hue={7}
      data-testid="tokens-and-cost"
      action={
        <a href={csvUrl(scope, group, filtre)} className="text-xs text-ink-muted hover:text-ink" download>
          CSV
        </a>
      }
    >
      {!report.data ? (
        <ReadState lecture={report} quoi="the tokens and the cost" />
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-x-10 gap-y-3" data-testid="token-totals">
            <Stat value={<span data-testid="total-input">{tokenCount(totals.input)}</span>} label="Input tokens" />
            <Stat value={<span data-testid="total-output">{tokenCount(totals.output)}</span>} label="Output tokens" />
            <Stat
              value={<span data-testid="total-cached">{tokenCount(totals.cached)}</span>}
              label="Cached input tokens"
              hint="Part of the input, read from the model's cache"
            />
            <Stat value={<span data-testid="total-cost">{money(cost ?? 0, currency)}</span>} label="Cost" />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div role="group" aria-label="Group by" className="flex flex-wrap gap-1">
              {groups.map((g) => (
                <button
                  key={g}
                  type="button"
                  aria-pressed={group === g}
                  onClick={() => setGroup(g)}
                  className={buttonClasses(group === g ? "primary" : "ghost", "sm")}
                >
                  {GROUP_LABELS[g]}
                </button>
              ))}
            </div>
            <div role="group" aria-label="Measure" className="flex gap-1">
              {MEASURES.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  aria-pressed={measure === m.id}
                  onClick={() => setMeasure(m.id)}
                  className={buttonClasses(measure === m.id ? "primary" : "ghost", "sm")}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>
          {report.data.rows.length === 0 ? (
            <Empty>Nothing spent here in this period.</Empty>
          ) : (
            <BreakdownTable
              rows={report.data.rows}
              currency={currency}
              measure={measure}
              label={`Tokens and cost, ${GROUP_LABELS[group].toLowerCase()}`}
              testId={`panel-${group}`}
              n={8}
            />
          )}
        </div>
      )}
    </Card>
  );
}
