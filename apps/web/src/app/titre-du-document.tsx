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
 * Every page has its own title (S23-05): every tab carried the same title, the browser's history
 * told no page apart, and a screen reader announced the same title on every navigation
 * (WCAG 2.4.2). React 19 hoists this `<title>` into the `<head>`.
 */
export function TitreDuDocument() {
  return <title>{titreDuChemin(usePathname())}</title>;
}
