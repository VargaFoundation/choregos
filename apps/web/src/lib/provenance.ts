// SPDX-License-Identifier: Apache-2.0
/**
 * D'où vient un signal (ADR 0045, S25-03) : déclaré par l'agent, constaté par la plateforme, déduit
 * d'autres signaux, ou inconnu. La console ne confond jamais ce qu'un agent DIT avec ce que la
 * plateforme CONSTATE : chaque élément du résumé d'un run porte sa provenance, et un run ne se dit
 * pas fini sur la seule parole de son agent.
 */

export type Provenance = "declared" | "observed" | "inferred" | "unknown";

/** La provenance d'une preuve : `measured` la dit, champ par champ ; sans elle, on ne sait pas. */
export function provenanceDePreuve(evidence: { measured?: string[] | null } | null | undefined, cle: string): Provenance {
  const mesures = evidence?.measured;
  if (!mesures) return "unknown";
  return mesures.includes(cle) ? "observed" : "declared";
}

type Verdict = { passed: boolean; pending: boolean };

/**
 * Ce que l'agent dit de sa fin, confronté à ce que la plateforme en constate : rien à dire quand
 * l'agent ne se dit pas fini ; « non constaté » quand une garantie a refusé ; « non vérifié » quand
 * aucune n'a jugé, ou qu'une juge encore.
 */
export function finDeclaree(statutDeclare: string | null | undefined, verdicts: Verdict[]): string | null {
  if (statutDeclare !== "done") return null;
  if (verdicts.some((v) => !v.passed && !v.pending)) return "declared done, not observed";
  if (verdicts.length === 0 || verdicts.some((v) => v.pending)) return "declared done, not verified yet";
  return null;
}
