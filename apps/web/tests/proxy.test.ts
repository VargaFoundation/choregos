// @vitest-environment node
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import { proxy } from "@/proxy";

/** La CSP que la console envoie, directive par directive. */
function directives(): Map<string, string[]> {
  const csp = proxy(new NextRequest("https://choregos.example/p/billing-api")).headers.get("Content-Security-Policy") ?? "";
  return new Map(
    csp
      .split(";")
      .map((d) => d.trim().split(/\s+/))
      .filter((morceaux) => morceaux[0])
      .map(([nom, ...valeurs]) => [nom!, valeurs]),
  );
}

describe("la politique de sécurité du contenu (S21-05)", () => {
  it("n'ouvre aucune origine tierce aux scripts, styles et polices : tout ce que la console charge vient d'elle", () => {
    const csp = directives();
    for (const nom of ["script-src", "style-src", "font-src", "worker-src", "default-src"]) {
      const tiers = (csp.get(nom) ?? []).filter((v) => /^(https?:|\*|[a-z0-9.-]+\.[a-z]{2,})/i.test(v));
      expect(tiers, nom).toEqual([]);
    }
  });

  it("garde les scripts sous nonce, sans unsafe-inline", () => {
    const scripts = directives().get("script-src") ?? [];
    expect(scripts.some((v) => v.startsWith("'nonce-"))).toBe(true);
    expect(scripts).not.toContain("'unsafe-inline'");
  });
});
