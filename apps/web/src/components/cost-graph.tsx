// SPDX-License-Identifier: Apache-2.0
import { usd as usdBrut } from "@/lib/format";
import { amount, type Currency, money } from "@/lib/spend";
import type { CostReport } from "@/lib/types";

type CostRow = CostReport["rows"][number];

function dayLabel(cle: string): string {
  const date = new Date(`${cle}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return cle;
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" }).format(date);
}

/**
 * The spend per day (S23-10). Before: fourteen bars with no date, no scale and no value, a
 * shape, not a measure; a bar's value could only be read on hover. Here the scale (the most expensive
 * day) on top, the first and last day below, and the table of values to unfold,
 * which is also what a screen reader reads.
 */
export function CostGraph({ rows, currency }: { rows: CostRow[]; currency?: Currency }) {
  // Dollars unless the page says otherwise: the project overview still reads the ledger in USD.
  const value = (row: CostRow) => (currency ? amount(row, currency) : row.cost_usd);
  const usd = (value: number) => (currency ? money(value, currency) : usdBrut(value));
  const max = Math.max(0, ...rows.map(value));
  const pic = rows.find((row) => value(row) === max);
  const premier = rows[0];
  const last = rows.at(-1);
  return (
    <div data-testid="cost-graph">
      <p className="mb-1 text-xs tabular-nums text-ink-muted" aria-hidden>
        {usd(max)}
      </p>
      <div
        className="flex h-40 items-end gap-1.5 border-t border-dashed border-t-line border-b border-b-ink"
        role="img"
        aria-label={`Cost per day, ${rows.length} days${pic ? `: highest ${usd(max)} on ${dayLabel(pic.key)}` : ""}`}
      >
        {rows.map((row) => (
          <div
            key={row.key}
            title={`${dayLabel(row.key)} — ${usd(value(row))} (${row.runs} runs)`}
            style={{ height: `${max > 0 ? Math.max(3, (value(row) / max) * 100) : 3}%` }}
            className="flex-1 bg-agent transition-opacity hover:opacity-70"
          />
        ))}
      </div>
      {premier && last && (
        <p className="mt-1 flex justify-between text-xs text-ink-muted" aria-hidden>
          <span>{dayLabel(premier.key)}</span>
          <span>{dayLabel(last.key)}</span>
        </p>
      )}
      <details className="mt-2">
        <summary className="cursor-pointer text-xs text-ink-muted">As a table</summary>
        <div className="overflow-x-auto">
          <table aria-label="Cost per day">
            <thead>
              <tr>
                <th>Day</th>
                <th className="text-right">Cost</th>
                <th className="text-right">Runs</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <td>{dayLabel(row.key)}</td>
                  <td className="text-right tabular-nums">{usd(value(row))}</td>
                  <td className="text-right tabular-nums">{row.runs}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
