// @vitest-environment node
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

// Tailwind 4 recopie la valeur arbitraire telle quelle : `grid-cols-[2fr,1fr]` devient
// `grid-template-columns: 2fr,1fr`, que le navigateur rejette — la grille retombe sur une seule
// colonne et le panneau de côté passe SOUS l'éditeur (vu sur le dev, 2026-10-07). Les pistes se
// séparent par `_` ; une virgule n'est permise que dans une fonction (`minmax(0,1fr)`, `repeat(…)`).

const RACINE = join(import.meta.dirname, "../src");
const GRILLE = /(?:[\w-]+:)*grid-(?:cols|rows)-\[([^\]]+)\]|\[grid-template-(?:columns|rows):([^\]]+)\]/g;

function fichiers(dossier: string): string[] {
  return readdirSync(dossier).flatMap((nom) => {
    const chemin = join(dossier, nom);
    if (statSync(chemin).isDirectory()) return fichiers(chemin);
    return /\.(tsx?|css)$/.test(nom) ? [chemin] : [];
  });
}

/** Ce qui reste d'une valeur une fois retirés les groupes entre parenthèses, imbriqués compris. */
function horsDesFonctions(valeur: string): string {
  let reste = valeur;
  for (let avant = ""; avant !== reste; ) {
    avant = reste;
    reste = reste.replace(/\([^()]*\)/g, "");
  }
  return reste;
}

function grillesAVirgule(source: string): string[] {
  return [...source.matchAll(GRILLE)]
    .filter((m) => horsDesFonctions(m[1] ?? m[2] ?? "").includes(","))
    .map((m) => m[0]);
}

describe("les grilles arbitraires de Tailwind 4", () => {
  it("le détecteur reconnaît une virgule entre pistes, pas dans une fonction", () => {
    expect(grillesAVirgule('className="lg:grid-cols-[2fr,1fr]"')).toEqual(["lg:grid-cols-[2fr,1fr]"]);
    expect(grillesAVirgule('className="[grid-template-columns:1fr,2fr]"')).toHaveLength(1);
    expect(grillesAVirgule('className="lg:grid-cols-[2fr_1fr] md:grid-cols-[minmax(0,1fr)_17rem]"')).toEqual([]);
    expect(grillesAVirgule('className="grid-cols-[repeat(auto-fill,minmax(12rem,1fr))]"')).toEqual([]);
  });

  it("aucune grille ne sépare ses pistes par une virgule : Tailwind 4 en fait une règle invalide", () => {
    const fautes = fichiers(RACINE).flatMap((chemin) =>
      grillesAVirgule(readFileSync(chemin, "utf8")).map((classe) => `${relative(RACINE, chemin)}: ${classe}`),
    );
    expect(fautes).toEqual([]);
  });
});
