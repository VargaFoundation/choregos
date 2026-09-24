"use client";

import { Card } from "@varga/design-system";
import { Empty } from "@/components/ui";
import type { RunEventDto } from "@/lib/types";

export type Verdict = {
  name: string;
  passed: boolean;
  pending: boolean;
  detail: string;
  annotations?: string[];
};

/** Les verdicts journalisés par l'orchestrateur (`gate.outcome`), dans l'ordre d'évaluation. */
export function verdictsDe(events: RunEventDto[]): Verdict[] {
  return events
    .filter((event) => event.type === "gate.outcome")
    .map((event) => {
      const p = (event.payload ?? {}) as Record<string, unknown>;
      return {
        name: String(p.name ?? ""),
        passed: Boolean(p.passed),
        pending: Boolean(p.pending),
        detail: String(p.detail ?? ""),
        annotations: Array.isArray(p.annotations) ? p.annotations.map(String) : [],
      };
    });
}

/**
 * Les garanties d'une étape, et ce qu'elles ont répondu.
 *
 * « Une garantie est un mécanisme, jamais un prompt » (ADR 0010) — mais avant le
 * 2026-09-24 aucun écran ne montrait quelles garanties avaient tourné ni leur verdict :
 * la promesse centrale du produit était invisible. Un refus passe devant, avec sa raison ;
 * une garantie qui attend (CI, review) le dit ; et ce que la garantie a REFUSÉ DE JUGER
 * (pas de diff, pas de sortie déclarée) se lit dans son détail, pas dans un ✓ rassurant.
 */
export function Garanties({ events }: { events: RunEventDto[] }) {
  const verdicts = verdictsDe(events);
  const refusees = verdicts.filter((v) => !v.passed && !v.pending).length;
  return (
    <Card
      title="garanties"
      action={
        <span className="text-xs text-ink-muted">
          {verdicts.length === 0 ? "—" : `${verdicts.length} évaluée(s) · ${refusees} refus`}
        </span>
      }
    >
      {verdicts.length === 0 ? (
        <Empty>aucune garantie évaluée sur ce run (la transition n&apos;en déclare pas, ou l&apos;étape n&apos;a pas abouti).</Empty>
      ) : (
        <ul className="space-y-2">
          {[...verdicts]
            .sort((a, b) => Number(a.passed || a.pending) - Number(b.passed || b.pending))
            .map((verdict, index) => (
              <li key={`${verdict.name}-${index}`} className="flex items-start gap-3 text-sm">
                <span
                  aria-label={verdict.pending ? "en attente" : verdict.passed ? "acceptée" : "refusée"}
                  className={verdict.pending ? "text-warn" : verdict.passed ? "text-ok" : "text-danger"}
                >
                  {verdict.pending ? "…" : verdict.passed ? "✓" : "✗"}
                </span>
                <span>
                  <span className="font-mono text-xs">{verdict.name}</span>
                  {verdict.detail && <span className="block text-xs text-ink-muted">{verdict.detail}</span>}
                  {(verdict.annotations ?? []).length > 0 && (
                    <span className="block text-xs text-ink-muted">{verdict.annotations?.join(" · ")}</span>
                  )}
                </span>
              </li>
            ))}
        </ul>
      )}
    </Card>
  );
}
