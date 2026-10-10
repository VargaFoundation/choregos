// @vitest-environment node
/**
 * Une classe de couleur qui ne nomme aucune couleur du thème ne fait rien : Tailwind n'en génère pas
 * de règle, et le filet retombe sur la couleur du texte, le fond sur rien. `border-l-waiting-solid` en
 * était une (S24-04) — les tons pleins s'écrivent `waiting`, `failed`… — : le filet d'une attente était
 * gris, celui d'un refus aussi. Les tests d'apparence vérifiaient le NOM de la classe, pas qu'elle
 * existe. Celui-ci lit les couleurs du thème (`--color-*` de `globals.css` et du design system) et
 * refuse, dans le code de la console, toute classe de couleur d'une de leurs familles qui n'en nomme
 * aucune. Les couleurs de Tailwind (`red-500`…), les tailles et les largeurs ne le regardent pas.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const WEB = join(import.meta.dirname, "..");
const RACINE = join(WEB, "src");
const FEUILLES = [join(RACINE, "app/globals.css"), join(WEB, "node_modules/@varga/design-system/css/theme.css")];

function fichiers(dossier: string): string[] {
  return readdirSync(dossier).flatMap((nom) => {
    const chemin = join(dossier, nom);
    if (statSync(chemin).isDirectory()) return fichiers(chemin);
    return /\.(tsx?|css)$/.test(nom) ? [chemin] : [];
  });
}

function couleursDuTheme(): Set<string> {
  const noms = new Set<string>();
  for (const feuille of FEUILLES) {
    for (const m of readFileSync(feuille, "utf8").matchAll(/--color-([a-z0-9-]+)\s*:/g)) noms.add(m[1]!);
  }
  return noms;
}

// `border-[xytrbl]` AVANT `border` : sinon `border-l-waiting-solid` se lirait `border` + `l-waiting-solid`,
// d'une famille `l` inconnue, et passerait.
const PROPRIETES = "bg|text|border-[xytrbl]|border|divide|outline|ring|fill|stroke|decoration|accent|caret|placeholder|from|via|to";
const CLASSE = new RegExp(`(?<![\\w-])(?:${PROPRIETES})-([a-z][a-z0-9-]*)(?:/\\d+)?(?![\\w-])`, "g");

/** Les classes de couleur d'un source qui visent une famille du thème sans en nommer une couleur. */
export function couleursInconnues(source: string, noms: Set<string>): string[] {
  const familles = new Set([...noms].map((nom) => nom.split("-")[0]!));
  return [...source.matchAll(CLASSE)]
    .filter((m) => familles.has(m[1]!.split("-")[0]!) && !noms.has(m[1]!))
    .map((m) => m[0]);
}

describe("les classes de couleur de la console", () => {
  it("le détecteur voit une couleur inconnue d'une famille du thème, et rien d'autre", () => {
    const noms = new Set(["waiting", "waiting-soft", "ink", "ink-muted", "line"]);
    const source =
      'className="border-l-waiting-solid hover:text-ink-muted bg-waiting-soft/50 text-xs border-l-2 bg-red-500 ' +
      'border-b-line/50 text-ink-mute"';
    expect(couleursInconnues(source, noms)).toEqual(["border-l-waiting-solid", "text-ink-mute"]);
  });

  it("chaque classe de couleur de la console nomme une couleur du thème", () => {
    const noms = couleursDuTheme();
    expect(noms.has("waiting")).toBe(true);
    const fautes = fichiers(RACINE).flatMap((chemin) =>
      couleursInconnues(readFileSync(chemin, "utf8"), noms).map((classe) => `${relative(RACINE, chemin)}: ${classe}`),
    );
    expect(fautes).toEqual([]);
  });
});
