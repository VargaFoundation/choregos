// @vitest-environment node
/**
 * D'où viennent les couleurs de la console (ADR 0043) : chacune est un pas des échelles Radix Colors
 * (MIT) — `--slate-3` vaut exactement le pas 3 de `slate` (clair) ou de `slateDark` (sombre) —, ou
 * `#fff` / `#000`. Aucune autre valeur n'entre : une teinte « au jugé » ne peut pas se glisser, et
 * l'allure se reconstruit depuis une source publique, sans rien emprunter d'ailleurs.
 *
 * Et chaque rôle de couleur que le design system déclare en sombre est redéclaré ici : une version
 * du design system qui en ajouterait un ne fuirait pas avec ses propres valeurs.
 */
import * as radix from "@radix-ui/colors";
import { describe, expect, it } from "vitest";
import { declarationsDe, FEUILLE_DU_THEME, JETONS_DU_DS } from "./jetons";

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

  it("chaque rôle de couleur du design system est redéclaré par la console", () => {
    const roles = [...declarationsDe(JETONS_DU_DS, ':root[data-theme="dark"]').keys()];
    expect(roles.length).toBeGreaterThan(15);
    const console = declarationsDe(FEUILLE_DU_THEME, ":root");
    // La marque et le cadre du logo restent ceux de la fondation : c'est son nom qu'ils portent.
    const manquants = roles.filter((role) => !["--varga-brand", "--varga-mark-frame"].includes(role) && !console.has(role));
    expect(manquants).toEqual([]);
  });
});
