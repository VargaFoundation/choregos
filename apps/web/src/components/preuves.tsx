"use client";

import { Card } from "@varga/design-system";
import { Empty } from "@/components/ui";
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
 */
export function Preuves({ evidence }: { evidence?: Evidence }) {
  const faits = Object.entries(evidence?.facts ?? {});
  const logiciel: [string, string][] = [];
  if (evidence?.tests_run != null) {
    logiciel.push(["tests", `${evidence.tests_passed ? "✓" : "✗"} ${evidence.tests_run}`]);
  }
  if (evidence?.lint) logiciel.push(["lint", evidence.lint]);
  if (evidence?.typecheck) logiciel.push(["typing", evidence.typecheck]);
  if (evidence?.coverage_delta != null) {
    logiciel.push(["coverage", `${evidence.coverage_delta > 0 ? "+" : ""}${evidence.coverage_delta}`]);
  }
  const lignes = faits.length > 0 ? faits : logiciel;

  return (
    <Card title="evidence">
      {lignes.length === 0 ? (
        <Empty>no evidence recorded.</Empty>
      ) : (
        <ul className="space-y-1 text-sm">
          {lignes.map(([nom, valeur]) => (
            <li key={nom}>
              {nom.replace(/_/g, " ")}:{" "}
              {typeof valeur === "boolean" ? (valeur ? "✓" : "✗") : String(valeur)}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
