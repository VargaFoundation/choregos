// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { Card, Empty, ReadState } from "@/components/ui";
import { api, type CostFilter } from "@/lib/api";
import type { CostReport } from "@/lib/types";
import { type Currency, type Measure, money, tokenCount, top, topTokens } from "@/lib/spend";

/** Where the spend is read: the whole organisation, or one project (the same report, narrower). */
export type Scope = { org: string; project: string | null };

export const reportKey = (scope: Scope, group: string, filtre: CostFilter) =>
  [
    "spend",
    scope.org,
    scope.project,
    group,
    filtre.kind ?? "",
    filtre.since ?? "",
    filtre.until ?? "",
    filtre.agent ?? "",
    filtre.workflow ?? "",
    filtre.work_item ?? "",
  ] as const;

export function fetchReport(scope: Scope, group: string, filtre: CostFilter) {
  return scope.project ? api.costs(scope.project, group, filtre) : api.orgCosts(scope.org, group, filtre);
}

export function csvUrl(scope: Scope, group: string, filtre: CostFilter): string {
  return scope.project ? api.costsCsvUrl(scope.project, group, filtre) : api.orgCostsCsvUrl(scope.org, group, filtre);
}

/**
 * The rows of one grouping, the heaviest first, each with its share drawn as a bar AND written as a
 * number: the colour carries nothing the text does not. In money, a row's amount; in tokens, its
 * input, output and cached tokens (the cached ones are part of the input), the share of input plus
 * output.
 */
export function BreakdownTable({
  rows,
  currency,
  measure,
  label,
  testId,
  n = 5,
}: {
  rows: CostReport["rows"];
  currency: Currency;
  measure: Measure;
  label: string;
  testId?: string;
  n?: number;
}) {
  const lines =
    measure === "tokens"
      ? topTokens(rows, n).map((row) => ({
          key: row.key,
          share: row.share,
          figure: (
            <span data-testid="row-tokens">
              {tokenCount(row.input)} in · {tokenCount(row.output)} out · {tokenCount(row.cached)} cached
            </span>
          ),
        }))
      : top(rows, currency, n).map((row) => ({ key: row.key, share: row.share, figure: money(row.value, currency) }));
  return (
    <table aria-label={label} className="w-full text-sm" data-testid={testId} data-measure={measure}>
      <tbody>
        {lines.map((row) => (
          <tr key={row.key} className="border-t border-line first:border-t-0">
            <th scope="row" className="w-2/5 py-1.5 pr-3 text-left font-normal">
              <span className="break-all" data-verbatim>
                {row.key}
              </span>
            </th>
            <td className="w-1/4 py-1.5">
              <div className="h-2 rounded-sm bg-surface-muted" aria-hidden>
                <div className="h-2 rounded-sm bg-agent" style={{ width: `${Math.max(2, row.share * 100)}%` }} />
              </div>
            </td>
            <td className="py-1.5 pl-3 text-right tabular-nums">{row.figure}</td>
            <td className="py-1.5 pl-2 text-right text-xs tabular-nums text-ink-muted">{Math.round(row.share * 100)} %</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * One breakdown of the spend: the heaviest rows of a grouping, in money or in tokens, and the CSV of
 * exactly that grouping and filter.
 */
export function BreakdownCard({
  scope,
  currency,
  title,
  group,
  filtre,
  measure = "cost",
  n = 5,
}: {
  scope: Scope;
  currency: Currency;
  title: string;
  group: string;
  filtre: CostFilter;
  measure?: Measure;
  n?: number;
}) {
  const report = useQuery({ queryKey: reportKey(scope, group, filtre), queryFn: () => fetchReport(scope, group, filtre) });
  const rows = report.data?.rows ?? [];
  return (
    <Card
      title={title}
      action={
        <a href={csvUrl(scope, group, filtre)} className="text-xs text-ink-muted hover:text-ink" download>
          CSV
        </a>
      }
    >
      {!report.data ? (
        <ReadState lecture={report} quoi={title} />
      ) : rows.length === 0 ? (
        <Empty>Nothing spent here in this period.</Empty>
      ) : (
        <BreakdownTable
          rows={rows}
          currency={currency}
          measure={measure}
          label={title}
          n={n}
          testId={`ventilation-${group}${filtre.kind ? `-${filtre.kind}` : ""}`}
        />
      )}
    </Card>
  );
}
