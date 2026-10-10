// SPDX-License-Identifier: Apache-2.0
"use client";

import { Provenance } from "@/components/provenance";
import { Card, Empty } from "@/components/ui";
import { provenanceDePreuve } from "@/lib/provenance";
import type { Run } from "@/lib/types";

type Evidence = NonNullable<NonNullable<Run["result"]>["evidence"]>;

/**
 * Les preuves d'une étape.
 *
 * Un dossier instruit, une présélection de profils ou un courrier n'ont ni tests ni
 * couverture : afficher « tests : ✗ · lint : — · typage : — » pour eux était faux, et
 * la faute était visible à l'écran avant d'être visible dans le contrat. Les faits que
 * le métier a nommés (`evidence.facts`) passent donc devant, et les mesures du logiciel
 * ne s'affichent que si elles ont été renseignées.
 *
 * Chaque ligne dit d'où elle vient (ADR 0045) : constatée si la plateforme l'a mesurée
 * (`evidence.measured`), déclarée si elle ne vient que du récit de l'agent.
 */
export function Preuves({ evidence }: { evidence?: Evidence }) {
  // [libellé, valeur, champ du contrat] : le champ dit, par `measured`, qui l'a établi.
  const faits: [string, string | number | boolean, string][] = Object.entries(evidence?.facts ?? {}).map(
    ([nom, valeur]) => [nom, valeur, `facts.${nom}`],
  );
  const logiciel: [string, string, string][] = [];
  if (evidence?.tests_run != null) {
    logiciel.push(["tests", `${evidence.tests_passed ? "✓" : "✗"} ${evidence.tests_run}`, "tests_passed"]);
  }
  if (evidence?.lint) logiciel.push(["lint", evidence.lint, "lint"]);
  if (evidence?.typecheck) logiciel.push(["typing", evidence.typecheck, "typecheck"]);
  if (evidence?.coverage_delta != null) {
    logiciel.push(["coverage", `${evidence.coverage_delta > 0 ? "+" : ""}${evidence.coverage_delta}`, "coverage_delta"]);
  }
  const lignes = faits.length > 0 ? faits : logiciel;

  return (
    <Card title="evidence">
      {lignes.length === 0 ? (
        <Empty>No evidence recorded.</Empty>
      ) : (
        <ul className="space-y-1 text-sm">
          {lignes.map(([nom, valeur, cle]) => (
            <li key={nom} className="flex items-baseline justify-between gap-2">
              <span>
                {nom.replace(/_/g, " ")}: {typeof valeur === "boolean" ? (valeur ? "✓" : "✗") : String(valeur)}
              </span>
              <Provenance de={provenanceDePreuve(evidence, cle)} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
