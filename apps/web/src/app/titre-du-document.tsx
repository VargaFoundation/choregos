// SPDX-License-Identifier: Apache-2.0
"use client";

import { usePathname } from "next/navigation";
import { useServedTheme } from "@/components/served-theme";

const SINGULIERS: Record<string, string> = { items: "ticket", runs: "run", actions: "action" };

/**
 * Le titre d'une page, depuis son chemin, du plus précis au plus large :
 * `/p/billing-api/items/w1` → « ticket w1 · billing-api · choregos ».
 */
export function titreDuChemin(pathname: string, productName = "Choregos"): string {
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
  // The suffix is the product's name; the default stays lower-case so that titles do not change.
  const suffix = productName === "Choregos" ? "choregos" : productName;
  return [...parties.reverse(), ...(parties.length ? [] : ["projects"]), suffix].join(" · ");
}

/**
 * Every page has its own title (S23-05): every tab carried the same title, the browser's history
 * told no page apart, and a screen reader announced the same title on every navigation
 * (WCAG 2.4.2). React 19 hoists this `<title>` into the `<head>`.
 */
export function TitreDuDocument() {
  return <title>{titreDuChemin(usePathname(), useServedTheme().product_name)}</title>;
}
