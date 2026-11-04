// SPDX-License-Identifier: Apache-2.0
"use client";

import { Provenance } from "@/components/provenance";
import { Card, Empty } from "@/components/ui";
import { toSentenceCase } from "@/lib/format";
import { evidenceProvenance } from "@/lib/provenance";
import type { Run } from "@/lib/types";

type Evidence = NonNullable<NonNullable<Run["result"]>["evidence"]>;

/**
 * A step's evidence.
 *
 * A processed file, a profile shortlist or a letter have neither tests nor
 * coverage: showing "tests: ✗ · lint: - · typing: -" for them was wrong, and
 * the mistake was visible on screen before being visible in the contract. The facts
 * the trade has named (`evidence.facts`) therefore come first, and the software's measures
 * are shown only if they were filled in.
 *
 * Each line says where it comes from (ADR 0045): observed if the platform measured it
 * (`evidence.measured`), declared if it comes only from the agent's account.
 */
export function EvidencePanel({ evidence }: { evidence?: Evidence }) {
  // [label, value, contract field]: the field says, through `measured`, who established it.
  const facts: [string, string | number | boolean, string][] = Object.entries(evidence?.facts ?? {}).map(
    ([nom, valeur]) => [nom, valeur, `facts.${nom}`],
  );
  const software: [string, string, string][] = [];
  if (evidence?.tests_run != null) {
    software.push(["tests", `${evidence.tests_passed ? "✓" : "✗"} ${evidence.tests_run}`, "tests_passed"]);
  }
  if (evidence?.lint) software.push(["lint", evidence.lint, "lint"]);
  if (evidence?.typecheck) software.push(["typing", evidence.typecheck, "typecheck"]);
  if (evidence?.coverage_delta != null) {
    software.push(["coverage", `${evidence.coverage_delta > 0 ? "+" : ""}${evidence.coverage_delta}`, "coverage_delta"]);
  }
  const rows = facts.length > 0 ? facts : software;

  return (
    <Card title="Evidence">
      {rows.length === 0 ? (
        <Empty>No evidence recorded.</Empty>
      ) : (
        <ul className="space-y-1 text-sm">
          {rows.map(([nom, valeur, cle]) => (
            <li key={nom} className="flex items-baseline justify-between gap-2">
              <span>
                {toSentenceCase(nom.replace(/_/g, " "))}: {typeof valeur === "boolean" ? (valeur ? "✓" : "✗") : String(valeur)}
              </span>
              <Provenance de={evidenceProvenance(evidence, cle)} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
