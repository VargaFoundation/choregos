// SPDX-License-Identifier: Apache-2.0
"use client";

import { tokens, usd } from "@/lib/format";
import type { ModelMatrix } from "@/lib/types";

type Entry = ModelMatrix["entries"][number];

/** A token median from the ledger, or a dash: a cell nothing was measured for shows no number. */
const measured = (value: number | null | undefined) => (value == null ? "—" : tokens(value));

/**
 * The published eval matrix: backend × model × memory × context reduction (S12-02, S37-09).
 * Tokens and cost are the gateway's ledger, as medians over the cases it measured (`metered`).
 */
export function EvalMatrixTable({ entries }: { entries: Entry[] }) {
  return (
    <div className="overflow-x-auto">
      <table data-testid="eval-matrix">
        <thead>
          <tr>
            <th>Backend</th>
            <th>Model</th>
            <th>Validated</th>
            <th className="text-right">Success</th>
            <th className="text-right">Median cost</th>
            <th>Memory</th>
            <th>Context</th>
            <th className="text-right" title="input tokens, cache reads included">Tokens in</th>
            <th className="text-right">Cache read</th>
            <th className="text-right">Tokens out</th>
            <th className="text-right">Turns</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, index) => (
            <tr key={index} data-context={entry.context ?? undefined}>
              <td>{entry.backend}</td>
              <td className="font-mono text-xs">{entry.model}</td>
              <td>{entry.validated ? "✓" : <span className="text-danger">✗</span>}</td>
              <td className="text-right">
                {entry.success_rate != null ? `${Math.round(entry.success_rate * 100)} %` : "—"}
              </td>
              <td className="text-right">{entry.median_cost_usd != null ? usd(entry.median_cost_usd) : "—"}</td>
              <td>{entry.with_memory == null ? "—" : entry.with_memory ? "With" : "Without"}</td>
              <td className="font-mono text-xs">{entry.context ?? "—"}</td>
              <td className="text-right">{measured(entry.median_tokens_in)}</td>
              <td className="text-right">{measured(entry.median_tokens_cached)}</td>
              <td className="text-right">{measured(entry.median_tokens_out)}</td>
              <td className="text-right">{entry.median_turns ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
