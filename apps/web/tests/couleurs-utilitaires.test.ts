// @vitest-environment node
/**
 * A colour class that names no theme colour does nothing: Tailwind generates no rule for it, and the
 * border falls back to the text colour, the background to nothing. `border-l-waiting-solid` was one
 * (S24-04) — the solid tones are spelt `waiting`, `failed`… —: a wait's border was grey, and so was a
 * refusal's. The appearance tests checked the class's NAME, not that it exists. This one reads the
 * theme's colours (`--color-*` in `globals.css`) and refuses, in the console's code, any colour class
 * of one of their families that names none of them. Tailwind's colours (`red-500`…), sizes and widths
 * are none of its business.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const WEB = join(import.meta.dirname, "..");
const RACINE = join(WEB, "src");
// The console's colours are declared in its own stylesheet only (ADR 0048).
const FEUILLES = [join(RACINE, "app/globals.css")];

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
