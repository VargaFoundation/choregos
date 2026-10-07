import { describe, expect, it } from "vitest";
import * as fixtures from "@/mocks/data";

// Le mode démo est la vitrine de la console : tout ce qu'il montre est en anglais, comme
// l'interface (revue du 07/10 : « que de l'anglais, pour toute la plateforme et pour la démo »).
// Les noms propres gardent leurs accents.
const NOMS_PROPRES = ["Léa"];
const ACCENTS = /[àâçéèêëîïôûùüÿœ]/i;
const MOTS_FRANCAIS = /\b(les|des|une|est|vers|pour|dans|avec|aux|pas|sont|nous|votre|tentative|périmètre)\b/i;

/** Chaque chaîne d'une valeur, avec son chemin, clés comprises quand elles se lisent à l'écran. */
function chaines(valeur: unknown, chemin: string, vues = new Set<unknown>()): [string, string][] {
  if (typeof valeur === "string") return [[chemin, valeur]];
  if (typeof valeur !== "object" || valeur === null || vues.has(valeur)) return [];
  vues.add(valeur);
  return Object.entries(valeur).flatMap(([cle, v]) => chaines(v, `${chemin}.${cle}`, vues));
}

function ressembleAuFrancais(texte: string): boolean {
  const sansNoms = NOMS_PROPRES.reduce((t, nom) => t.replaceAll(nom, ""), texte);
  return ACCENTS.test(sansNoms) || MOTS_FRANCAIS.test(sansNoms);
}

describe("les fixtures du mode démo", () => {
  it("le détecteur reconnaît le français et laisse passer l'anglais et les noms propres", () => {
    expect(ressembleAuFrancais("Les avoirs ne sont pas déduits du total")).toBe(true);
    expect(ressembleAuFrancais("hors du périmètre autorisé")).toBe(true);
    expect(ressembleAuFrancais("refine — tentative 1")).toBe(true);
    expect(ressembleAuFrancais("Order Léa's laptop (supplier)")).toBe(false);
    expect(ressembleAuFrancais("Credit notes are not deducted from the total")).toBe(false);
  });

  it("ne montrent pas de français", () => {
    const fautes = Object.entries(fixtures)
      .filter(([, valeur]) => typeof valeur !== "function")
      .flatMap(([nom, valeur]) => chaines(valeur, nom))
      .filter(([, texte]) => ressembleAuFrancais(texte))
      .map(([chemin, texte]) => `${chemin}: ${texte.slice(0, 80)}`);
    expect(fautes).toEqual([]);
  });
});
