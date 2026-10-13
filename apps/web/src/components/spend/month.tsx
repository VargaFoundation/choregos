// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge, Card, EtatDeLecture, Stat } from "@/components/ui";
import { type Currency, money, monthForecast, monthStart, usdToCurrency } from "@/lib/spend";
import { fetchReport, reportKey, type Scope } from "./breakdown";

/**
 * Budget against actual for the current month, with a linear forecast to month end. The budget is
 * the daily budget the policy declares, times the month's days: say so, since it is not a monthly
 * figure anyone wrote down.
 */
export function MonthPanel({ scope, currency, now }: { scope: Scope; currency: Currency; now: Date }) {
  const filtre = { since: monthStart(now) };
  const report = useQuery({ queryKey: reportKey(scope, "day", filtre), queryFn: () => fetchReport(scope, "day", filtre) });
  if (!report.data) {
    return (
      <Card title="This month">
        <EtatDeLecture lecture={report} quoi="this month's spend" />
      </Card>
    );
  }
  const forecast = monthForecast(report.data.rows, now, currency, report.data.total.budget_usd, currency === "USD" ? 1 : usdToCurrency(report.data.total));
  return (
    <Card title="This month" eyebrow="Budget against actual">
      <div className="flex flex-wrap items-end gap-x-10 gap-y-3" data-testid="mois">
        <Stat value={money(forecast.spent, currency)} label={`Spent in ${forecast.daysElapsed} of ${forecast.daysInMonth} days`} />
        <Stat value={money(forecast.forecast, currency)} label="Forecast at month end (linear)" />
        <Stat
          value={forecast.budget === null ? "—" : money(forecast.budget, currency)}
          label={forecast.budget === null ? "No daily budget set" : "Budget: daily budget × days in the month"}
        />
        {forecast.budget !== null && (
          <Badge tone={forecast.over ? "danger" : "ok"} data-testid="budget-etat">
            {forecast.over
              ? `Over budget${forecast.spent > forecast.budget ? "" : " by month end"}`
              : `${Math.round((forecast.ratio ?? 0) * 100)} % of the budget spent`}
          </Badge>
        )}
      </div>
    </Card>
  );
}
