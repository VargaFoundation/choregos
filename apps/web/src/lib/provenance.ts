// SPDX-License-Identifier: Apache-2.0
/**
 * Where a signal comes from (ADR 0045, S25-03): declared by the agent, observed by the platform,
 * inferred from other signals, or unknown. The console never confuses what an agent SAYS with what
 * the platform OBSERVES: every element of a run's summary carries its provenance, and a run is not
 * called finished on its agent's word alone.
 */

export type Provenance = "declared" | "observed" | "inferred" | "unknown";

/** The provenance of a piece of evidence: `measured` says it, field by field; without it, we do not know. */
export function evidenceProvenance(evidence: { measured?: string[] | null } | null | undefined, key: string): Provenance {
  const measured = evidence?.measured;
  if (!measured) return "unknown";
  return measured.includes(key) ? "observed" : "declared";
}

type Verdict = { passed: boolean; pending: boolean };

/**
 * What the agent says about its end, set against what the platform observes: nothing to say when
 * the agent does not say it is finished; "not observed" when a guarantee refused; "not verified"
 * when none has judged, or one is still judging.
 */
export function declaredEnd(declaredStatus: string | null | undefined, verdicts: Verdict[]): string | null {
  if (declaredStatus !== "done") return null;
  if (verdicts.some((v) => !v.passed && !v.pending)) return "declared done, not observed";
  if (verdicts.length === 0 || verdicts.some((v) => v.pending)) return "declared done, not verified yet";
  return null;
}
