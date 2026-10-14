// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { Card, Empty, EtatDeLecture } from "@/components/ui";
import { api, type CostFilter } from "@/lib/api";
import { type Currency, money, top } from "@/lib/spend";

/** Where the spend is read: the whole organisation, or one project (the same report, narrower). */
export type Scope = { org: string; project: string | null };

export const reportKey = (scope: Scope, group: string, filtre: CostFilter) =>
  ["spend", scope.org, scope.project, group, filtre.kind ?? "", filtre.since ?? "", filtre.until ?? ""] as const;

export function fetchReport(scope: Scope, group: string, filtre: CostFilter) {
  return scope.project ? api.costs(scope.project, group, filtre) : api.orgCosts(scope.org, group, filtre);
}

export function csvUrl(scope: Scope, group: string, filtre: CostFilter): string {
  return scope.project ? api.costsCsvUrl(scope.project, group, filtre) : api.orgCostsCsvUrl(scope.org, group, filtre);
}

/**
 * One breakdown of the spend: the most expensive rows of a grouping, each with its share, and the
 * CSV of exactly that grouping. The share is drawn as a bar AND written as a number: the colour
 * carries nothing the text does not.
 */
export function BreakdownCard({
  scope,
  currency,
  title,
  group,
  filtre,
  n = 5,
}: {
  scope: Scope;
  currency: Currency;
  title: string;
  group: string;
  filtre: CostFilter;
  n?: number;
}) {
  const report = useQuery({ queryKey: reportKey(scope, group, filtre), queryFn: () => fetchReport(scope, group, filtre) });
  const rows = top(report.data?.rows ?? [], currency, n);
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
        <EtatDeLecture lecture={report} quoi={title} />
      ) : rows.length === 0 ? (
        <Empty>Nothing spent here in this period.</Empty>
      ) : (
        <table aria-label={title} className="w-full text-sm" data-testid={`ventilation-${group}${filtre.kind ? `-${filtre.kind}` : ""}`}>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-t border-line first:border-t-0">
                <th scope="row" className="w-2/5 py-1.5 pr-3 text-left font-normal">
                  <span className="break-all" data-donnee>
                    {row.key}
                  </span>
                </th>
                <td className="w-1/3 py-1.5">
                  <div className="h-2 rounded-sm bg-surface-muted" aria-hidden>
                    <div className="h-2 rounded-sm bg-agent" style={{ width: `${Math.max(2, row.share * 100)}%` }} />
                  </div>
                </td>
                <td className="py-1.5 pl-3 text-right tabular-nums">{money(row.value, currency)}</td>
                <td className="py-1.5 pl-2 text-right text-xs tabular-nums text-ink-muted">{Math.round(row.share * 100)} %</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
