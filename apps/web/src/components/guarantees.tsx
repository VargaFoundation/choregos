// SPDX-License-Identifier: Apache-2.0
"use client";

import { Card, Empty } from "@/components/ui";
import type { RunEventDto } from "@/lib/types";
import { Provenance } from "@/components/provenance";

export type Verdict = {
  name: string;
  passed: boolean;
  pending: boolean;
  detail: string;
  annotations?: string[];
};

/** The verdicts logged by the orchestrator (`gate.outcome`), in the order of evaluation. */
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
 * A step's guarantees, and what they answered.
 *
 * "A guarantee is a mechanism, never a prompt" (ADR 0010), but before
 * 2026-09-24 no screen showed which guarantees had run nor their verdict:
 * the product's central promise was invisible. A refusal comes first, with its reason;
 * a guarantee that waits (CI, review) says so; and what the guarantee REFUSED TO JUDGE
 * (no diff, no declared output) is read in its detail, not in a reassuring tick.
 */
export function Guarantees({ events }: { events: RunEventDto[] }) {
  const verdicts = verdictsDe(events);
  const refusees = verdicts.filter((v) => !v.passed && !v.pending).length;
  return (
    <Card
      title="Gates"
      action={
        <span className="flex items-center gap-3 text-xs text-ink-muted">
          {verdicts.length === 0 ? "—" : `${verdicts.length} evaluated · ${refusees} refused`}
          <Provenance de="observed" />
        </span>
      }
    >
      {verdicts.length === 0 ? (
        <Empty>No gate evaluated on this run (the transition declares none, or the stage did not complete).</Empty>
      ) : (
        <ul className="space-y-2">
          {[...verdicts]
            .sort((a, b) => Number(a.passed || a.pending) - Number(b.passed || b.pending))
            .map((verdict, index) => (
              <li key={`${verdict.name}-${index}`} className="flex items-start gap-3 text-sm">
                <span
                  aria-label={verdict.pending ? "Pending" : verdict.passed ? "Passed" : "Refused"}
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
