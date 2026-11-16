// SPDX-License-Identifier: Apache-2.0
/**
 * What the Activity screen computes (S64-06, ADR 0142 §1): an entry's label, its elapsed time, what it
 * spent so far. Pure, so that the screen's words and numbers are checked without a browser.
 */
import { tokens, usd } from "./format";
import type { ActivityEntry } from "./types";

/** The longest reason a stop accepts (`StopRequest.reason`, 1-500 characters). */
export const REASON_MAX = 500;

/** What an entry is, in a few words: a run by its work item key and stage, a session, a turn. */
export function entryLabel(entry: ActivityEntry): string {
  if (entry.kind === "run") {
    const parts = [entry.work_item_key, entry.stage_role].filter(Boolean);
    return parts.length ? parts.join(" · ") : `Run ${entry.id.slice(0, 8)}`;
  }
  if (entry.kind === "session") return `Session ${entry.id.slice(0, 8)}`;
  return `Conversation turn ${entry.id.slice(0, 8)}`;
}

/** Milliseconds since the entry started, to its end once ended; never negative (a clock behind). */
export function elapsedMs(entry: Pick<ActivityEntry, "started_at" | "ended_at">, now: number): number {
  const start = new Date(entry.started_at).getTime();
  const end = entry.ended_at ? new Date(entry.ended_at).getTime() : now;
  return Math.max(0, end - start);
}

/** An elapsed time that ticks: seconds under a minute, minutes and seconds under an hour, then hours. */
export function formatElapsed(ms: number): string {
  const total = Math.floor(ms / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours) return `${hours} h ${String(minutes).padStart(2, "0")} min`;
  if (minutes) return `${minutes} min ${String(seconds).padStart(2, "0")} s`;
  return `${seconds} s`;
}

/**
 * What an entry spent so far: tokens in and out, and the cost. `null` when the gateway cannot say
 * (ADR 0141 §2): the screen then writes "—", never a zero it does not know.
 */
export function spendSoFar(entry: Pick<ActivityEntry, "spend">): { tokens: string; cost: string } {
  if (!entry.spend) return { tokens: "—", cost: "—" };
  return { tokens: `${tokens(entry.spend.tokens_in)} in · ${tokens(entry.spend.tokens_out)} out`, cost: usd(entry.spend.cost_usd) };
}

/** The tone family of an entry's status, for its badge (a word always goes with it). */
export function statusKind(status: string): "work" | "terminal" | "blocked" | "wait" {
  if (status === "succeeded" || status === "completed" || status === "closed") return "terminal";
  if (status === "failed" || status === "cancelled" || status === "timed_out") return "blocked";
  if (status === "queued") return "wait";
  return "work";
}
