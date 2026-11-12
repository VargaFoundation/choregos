// SPDX-License-Identifier: Apache-2.0
/**
 * The figures of a node: `elapsed · tokens · cost`. A figure the platform does not know is `—`, never 0
 * (ADR 0141 §2): `spend: null` means the gateway could not say.
 */
import type { ActivityEntry } from "@choregos/contracts/api";

export const UNKNOWN = "—";

export function elapsed(startedAt: string, endedAt: string | null | undefined, now: Date): string {
  const start = Date.parse(startedAt);
  const end = endedAt ? Date.parse(endedAt) : now.getTime();
  if (Number.isNaN(start) || Number.isNaN(end)) return UNKNOWN;
  const seconds = Math.max(0, Math.floor((end - start) / 1000));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

export function tokens(count: number | null | undefined): string {
  if (count === null || count === undefined) return UNKNOWN;
  if (count < 1000) return `${count} tok`;
  if (count < 1_000_000) return `${(count / 1000).toFixed(count < 10_000 ? 1 : 0)}k tok`;
  return `${(count / 1_000_000).toFixed(1)}M tok`;
}

export function money(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return UNKNOWN;
  return `$${usd < 10 ? usd.toFixed(2) : usd.toFixed(0)}`;
}

export function figures(entry: ActivityEntry, now: Date): string {
  const spend = entry.spend ?? null;
  const used = spend ? spend.tokens_in + spend.tokens_out : null;
  return [elapsed(entry.started_at, entry.ended_at, now), tokens(used), money(spend?.cost_usd)].join(" · ");
}
