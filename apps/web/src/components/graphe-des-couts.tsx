// SPDX-License-Identifier: Apache-2.0
import { usd } from "@/lib/format";
import type { CostReport } from "@/lib/types";

type CostRow = CostReport["rows"][number];

function jour(cle: string): string {
  const date = new Date(`${cle}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return cle;
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" }).format(date);
}

/**
 * La dépense par jour (S23-10). Avant : quatorze barres sans date, sans échelle ni valeur — une
 * forme, pas une mesure ; la valeur d'une barre ne se lisait qu'au survol. Ici l'échelle (le jour
 * le plus cher) en haut, le premier et le dernier jour dessous, et le tableau des valeurs à déplier,
 * qui est aussi ce qu'un lecteur d'écran lit.
 */
export function GrapheDesCouts({ rows }: { rows: CostRow[] }) {
  const max = Math.max(0, ...rows.map((row) => row.cost_usd));
  const pic = rows.find((row) => row.cost_usd === max);
  const premier = rows[0];
  const dernier = rows.at(-1);
  return (
    <div data-testid="graphe-des-couts">
      <p className="mb-1 text-xs tabular-nums text-ink-muted" aria-hidden>
        {usd(max)}
      </p>
      <div
        className="flex h-40 items-end gap-1.5 border-t border-dashed border-t-line border-b border-b-ink"
        role="img"
        aria-label={`cost per day, ${rows.length} days${pic ? `: highest ${usd(max)} on ${jour(pic.key)}` : ""}`}
      >
        {rows.map((row) => (
          <div
            key={row.key}
            title={`${jour(row.key)} — ${usd(row.cost_usd)} (${row.runs} runs)`}
            style={{ height: `${max > 0 ? Math.max(3, (row.cost_usd / max) * 100) : 3}%` }}
            className="flex-1 bg-agent transition-opacity hover:opacity-70"
          />
        ))}
      </div>
      {premier && dernier && (
        <p className="mt-1 flex justify-between text-xs text-ink-muted" aria-hidden>
          <span>{jour(premier.key)}</span>
          <span>{jour(dernier.key)}</span>
        </p>
      )}
      <details className="mt-2">
        <summary className="cursor-pointer text-xs text-ink-muted">as a table</summary>
        <div className="overflow-x-auto">
          <table aria-label="cost per day">
            <thead>
              <tr>
                <th>day</th>
                <th className="text-right">cost</th>
                <th className="text-right">runs</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <td>{jour(row.key)}</td>
                  <td className="text-right tabular-nums">{usd(row.cost_usd)}</td>
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
