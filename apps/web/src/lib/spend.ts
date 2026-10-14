// SPDX-License-Identifier: Apache-2.0
/**
 * What the spend screen computes (S29-03): which currency a row is read in, the month's forecast,
 * and the top of a breakdown. Pure, so the numbers on the screen can be checked without a browser.
 */
import type { CostReport } from "./types";

export type Currency = "EUR" | "USD";
type Row = Pick<CostReport["rows"][number], "key" | "cost_usd" | "cost_eur">;

export function amount(row: Pick<Row, "cost_usd" | "cost_eur">, currency: Currency): number {
  return currency === "USD" ? row.cost_usd : row.cost_eur;
}

export function money(value: number | null | undefined, currency: Currency): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(value);
}

/** The first day of `now`'s month, UTC, as `YYYY-MM-DD` — the ledger counts days in UTC. */
export function monthStart(now: Date): string {
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)).toISOString().slice(0, 10);
}

export function daysInMonth(now: Date): number {
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 0)).getUTCDate();
}

export type MonthForecast = {
  spent: number;
  /** Linear: what has been spent per elapsed day, carried to the month's last day. */
  forecast: number;
  /** The daily budget times the month's days, in the display currency; `null` when there is none. */
  budget: number | null;
  daysElapsed: number;
  daysInMonth: number;
  /** The forecast crosses the budget — or the spend already has. */
  over: boolean;
  /** Share of the budget already spent, 0..1+. */
  ratio: number | null;
};

/**
 * Budget against actual, with a linear forecast to month end. `dayRows` are the per-day rows from
 * the first of the month; the day in progress counts as a day, so the forecast is never made
 * from zero elapsed days. `dailyBudgetUsd` is the report's `total.budget_usd`, converted with
 * `usdTo` (the report's own EUR/USD ratio) when the display currency is not dollars.
 */
export function monthForecast(
  dayRows: Row[],
  now: Date,
  currency: Currency,
  dailyBudgetUsd: number | null | undefined,
  usdTo: number | null,
): MonthForecast {
  const spent = dayRows.reduce((sum, row) => sum + amount(row, currency), 0);
  const days = daysInMonth(now);
  const elapsed = Math.max(1, now.getUTCDate());
  const forecast = (spent / elapsed) * days;
  const rate = currency === "USD" ? 1 : usdTo;
  const budget = dailyBudgetUsd && rate !== null ? dailyBudgetUsd * rate * days : null;
  return {
    spent,
    forecast,
    budget,
    daysElapsed: elapsed,
    daysInMonth: days,
    over: budget !== null && Math.max(spent, forecast) > budget,
    ratio: budget ? spent / budget : null,
  };
}

/** The EUR per USD the ledger applied over a report, or `null` when nothing was spent. */
export function usdToCurrency(total: Pick<CostReport["total"], "cost_usd" | "cost_eur">): number | null {
  const usd = total.cost_usd ?? 0;
  return usd > 0 ? (total.cost_eur ?? 0) / usd : null;
}

/** The `n` most expensive rows, with each one's share of the total. */
export function top(rows: Row[], currency: Currency, n = 5): Array<{ key: string; value: number; share: number }> {
  const total = rows.reduce((sum, row) => sum + amount(row, currency), 0);
  return [...rows]
    .sort((a, b) => amount(b, currency) - amount(a, currency))
    .slice(0, n)
    .map((row) => ({ key: row.key, value: amount(row, currency), share: total > 0 ? amount(row, currency) / total : 0 }));
}
