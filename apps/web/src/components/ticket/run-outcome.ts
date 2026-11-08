// SPDX-License-Identifier: Apache-2.0
/**
 * Why a run ended, in words (S59-07). The cost table showed only the first characters of the summary, so
 * four triage runs read as four identical lines and the growing cost had no explanation.
 */

const REASONS: Record<string, string> = {
  max_turns: "stopped at its tool-call limit",
  max_minutes: "stopped at its time limit",
  limit: "stopped at a limit",
  budget: "stopped: budget spent",
  tests: "tests failed",
  lint: "lint failed",
  typecheck: "type check failed",
  ci: "CI failed",
  scope: "wrote outside its allowed paths",
  invalid_result: "returned no valid result",
  agent_error: "the agent failed",
  question: "asked a person",
  configuration: "configuration refused",
};

export type RunLike = { status?: string | null; result?: { status?: string | null; reason?: string | null } | null };

/** `done`, `failed` or the run's status, with the cause in words when it did not succeed. */
export function runOutcome(run: RunLike): { tone: "ok" | "bad" | "neutral"; label: string } {
  const status = run.result?.status ?? run.status ?? "";
  if (status === "done" || status === "succeeded") return { tone: "ok", label: "done" };
  const reason = run.result?.reason ?? null;
  if (status === "failed" || reason)
    return { tone: "bad", label: reason ? (REASONS[reason] ?? reason.replace(/_/g, " ")) : "failed" };
  return { tone: "neutral", label: status.replace(/_/g, " ") || "running" };
}
