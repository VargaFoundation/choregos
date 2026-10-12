// @vitest-environment node
/**
 * D'où viennent les couleurs de la console (ADR 0043) : chacune est un pas des échelles Radix Colors
 * (MIT) — `--slate-3` vaut exactement le pas 3 de `slate` (clair) ou de `slateDark` (sombre) —, ou
 * `#fff` / `#000`. Aucune autre valeur n'entre : une teinte « au jugé » ne peut pas se glisser, et
 * l'allure se reconstruit depuis une source publique, sans rien emprunter d'ailleurs.
 *
 * And every token the console reads (`var(--choregos-…)`, in its code and its stylesheets) is declared
 * by its theme: the console owns its tokens (ADR 0048), no other stylesheet supplies one, and a role
 * read but never declared would silently fall back to nothing.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import * as radix from "@radix-ui/colors";
import { describe, expect, it } from "vitest";
import { declarationsDe, FEUILLE_DU_THEME } from "./jetons";

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((nom) => {
    const chemin = join(dir, nom);
    if (statSync(chemin).isDirectory()) return sourceFiles(chemin);
    return /\.(tsx?|css)$/.test(nom) ? [chemin] : [];
  });
}

const ECHELLES = radix as unknown as Record<string, Record<string, string>>;
const AUTORISES = new Set(["#fff", "#000"]);
const BLOCS = [
  { selecteur: ":root", suffixe: "Dark" },
  { selecteur: ':root[data-theme="light"]', suffixe: "" },
] as const;

describe("la provenance des couleurs de la console", () => {
  it.each(BLOCS)("chaque pas nommé de $selecteur vaut le pas Radix du même nom", ({ selecteur, suffixe }) => {
    const pas = [...declarationsDe(FEUILLE_DU_THEME, selecteur)].filter(([nom]) => /^--[a-z]+-\d+$/.test(nom));
    expect(pas.length, "aucun pas Radix déclaré").toBeGreaterThan(10);
    for (const [nom, valeur] of pas) {
      const [, teinte, rang] = /^--([a-z]+)-(\d+)$/.exec(nom)!;
      const echelle = ECHELLES[`${teinte}${suffixe}`];
      expect(echelle, `échelle Radix inconnue : ${teinte}${suffixe}`).toBeDefined();
      expect(valeur.toLowerCase(), `${nom} en ${selecteur}`).toBe(echelle![`${teinte}${rang}`]);
    }
  });

  it.each(BLOCS)("aucune autre couleur littérale que #fff et #000 dans les rôles de $selecteur", ({ selecteur }) => {
    for (const [nom, valeur] of declarationsDe(FEUILLE_DU_THEME, selecteur)) {
      if (/^--[a-z]+-\d+$/.test(nom)) continue;
      for (const litteral of valeur.match(/#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)|oklch\([^)]*\)/g) ?? []) {
        expect(AUTORISES.has(litteral.toLowerCase()), `${nom} : ${litteral}`).toBe(true);
      }
    }
  });

  it("every token the console reads is declared by its theme", () => {
    const declared = new Set(declarationsDe(FEUILLE_DU_THEME, ":root").keys());
    // A card or the sidebar redefines the surface for what it holds: still a declared token.
    const read = new Set<string>();
    for (const chemin of sourceFiles(join(import.meta.dirname, "../src"))) {
      for (const m of readFileSync(chemin, "utf8").matchAll(/var\((--choregos-[a-z0-9-]+)/g)) read.add(m[1]!);
    }
    expect(read.size, "no token read: the test checks nothing").toBeGreaterThan(20);
    expect([...read].filter((nom) => !declared.has(nom)).sort()).toEqual([]);
  });

  it("no stylesheet of the console declares a colour role outside its theme", () => {
    const elsewhere = sourceFiles(join(import.meta.dirname, "../src"))
      .filter((chemin) => chemin.endsWith(".css") && chemin !== FEUILLE_DU_THEME)
      .flatMap((chemin) => [...readFileSync(chemin, "utf8").matchAll(/^\s*(--(?:choregos|varga)-[a-z0-9-]+)\s*:/gm)].map((m) => m[1]!));
    expect(elsewhere).toEqual([]);
  });
});
