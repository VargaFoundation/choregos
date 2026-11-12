// SPDX-License-Identifier: Apache-2.0
/**
 * A state is an icon, a word and a theme colour, never a colour alone (ADR 0155 §3, ADR 0120). The colours are
 * the editor's theme colours, so light, dark and high-contrast themes set the contrast.
 */
import type { ActivityEntry } from "@choregos/contracts/api";

export type State = "done" | "running" | "waiting" | "queued" | "failed" | "stopped";

export interface Look {
  word: string;
  icon: string;
  color: string;
}

export const LOOKS: Record<State, Look> = {
  done: { word: "done", icon: "pass", color: "charts.green" },
  running: { word: "running", icon: "sync~spin", color: "charts.blue" },
  waiting: { word: "waiting for you", icon: "bell", color: "charts.yellow" },
  queued: { word: "queued", icon: "circle-outline", color: "disabledForeground" },
  failed: { word: "failed", icon: "error", color: "charts.red" },
  stopped: { word: "stopped", icon: "debug-stop", color: "disabledForeground" },
};

/** Run (`RunSummary.status`), session (`open`, `closed`) and turn statuses, as `ActivityEntry.status` says. */
const BY_STATUS: Record<string, State> = {
  queued: "queued",
  running: "running",
  open: "running",
  succeeded: "done",
  completed: "done",
  closed: "done",
  failed: "failed",
  timed_out: "failed",
  cancelled: "stopped",
};

/** The state of an entry; a status this version does not know is shown as its own word, never guessed. */
export function stateOf(entry: Pick<ActivityEntry, "status">): State | undefined {
  return BY_STATUS[entry.status];
}

export function lookOf(entry: Pick<ActivityEntry, "status">): Look {
  const state = stateOf(entry);
  return state ? LOOKS[state] : { word: entry.status, icon: "question", color: "descriptionForeground" };
}
