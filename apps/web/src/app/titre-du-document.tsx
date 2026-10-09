// SPDX-License-Identifier: Apache-2.0
"use client";

import { usePathname } from "next/navigation";

const SINGULIERS: Record<string, string> = { items: "ticket", runs: "run", actions: "action" };

/**
 * Le titre d'une page, depuis son chemin, du plus précis au plus large :
 * `/p/billing-api/items/w1` → « ticket w1 · billing-api · choregos ».
 */
export function titreDuChemin(pathname: string): string {
  const segments = pathname.split("/").filter(Boolean).map(decodeURIComponent);
  if (segments[0] === "p") segments.shift();
  const parties: string[] = [];
  for (let i = 0; i < segments.length; i++) {
    const segment = segments[i]!;
    const suivant = segments[i + 1];
    if (SINGULIERS[segment] && suivant) {
      parties.push(`${SINGULIERS[segment]} ${suivant}`);
      i++;
    } else {
      parties.push(segment === "x" ? "section" : segment);
    }
  }
  return [...parties.reverse(), ...(parties.length ? [] : ["projects"]), "choregos"].join(" · ");
}

/**
 * Chaque page a son titre (S23-05) : tous les onglets s'appelaient « choregos · varga foundation »,
 * l'historique du navigateur ne distinguait aucune page, et un lecteur d'écran annonçait le même
 * titre à chaque navigation (WCAG 2.4.2). React 19 remonte ce `<title>` dans le `<head>`.
 */
export function TitreDuDocument() {
  return <title>{titreDuChemin(usePathname())}</title>;
}
