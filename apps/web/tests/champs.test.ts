// @vitest-environment node
/**
 * Aucun champ de saisie brut hors de `components/ui` (S24-04, ADR 0043).
 *
 * Un `<input>`, un `<select>` ou un `<textarea>` écrit dans une page portait son propre habillage :
 * arrondi ici, carré là, 28 ou 36 px de haut, un focus à l'encre, ou rien du tout — le rendu du
 * navigateur. 78 champs, autant de variantes. Ils passent par `Input`, `Select` et `Textarea` de
 * `components/ui`, qui portent le `CHAMP` de la console. Restent bruts les contrôles qui ne sont pas
 * des champs de saisie : case à cocher, bouton radio, fichier, curseur.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const RACINE = join(import.meta.dirname, "../src");
const PERMIS = new Set(["checkbox", "radio", "file", "range", "hidden", "color"]);
const BALISES = new Set(["input", "select", "textarea"]);

function fichiers(dossier: string): string[] {
  return readdirSync(dossier).flatMap((nom) => {
    const chemin = join(dossier, nom);
    if (statSync(chemin).isDirectory()) return fichiers(chemin);
    return chemin.endsWith(".tsx") ? [chemin] : [];
  });
}

/** Les champs de saisie bruts d'un source TSX : `fichier:ligne <balise>`. */
export function champsBruts(nom: string, texte: string): string[] {
  const source = ts.createSourceFile(nom, texte, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const trouves: string[] = [];
  const visiter = (noeud: ts.Node) => {
    if (ts.isJsxSelfClosingElement(noeud) || ts.isJsxOpeningElement(noeud)) {
      const balise = noeud.tagName.getText(source);
      if (BALISES.has(balise)) {
        const attribut = noeud.attributes.properties.find(
          (a): a is ts.JsxAttribute => ts.isJsxAttribute(a) && a.name.getText(source) === "type",
        );
        const type = attribut?.initializer && ts.isStringLiteral(attribut.initializer) ? attribut.initializer.text : undefined;
        if (!(balise === "input" && type && PERMIS.has(type))) {
          const { line } = source.getLineAndCharacterOfPosition(noeud.getStart(source));
          trouves.push(`${nom}:${line + 1} <${balise}${type ? ` type="${type}"` : ""}>`);
        }
      }
    }
    ts.forEachChild(noeud, visiter);
  };
  visiter(source);
  return trouves;
}

describe("les champs de saisie de la console", () => {
  it("passent tous par Input, Select ou Textarea de components/ui", () => {
    const bruts = fichiers(RACINE)
      .filter((chemin) => !chemin.endsWith(join("components", "ui.tsx")))
      .flatMap((chemin) => champsBruts(relative(RACINE, chemin), readFileSync(chemin, "utf8")));
    expect(bruts).toEqual([]);
  });

  it("la garde voit un champ brut, et laisse passer une case à cocher (elle ne passe pas à vide)", () => {
    const source = `export const A = () => (<form>
      <input value="x" className="rounded border" />
      <select><option>a</option></select>
      <textarea />
      <input type={secret ? "password" : "text"} />
      <input type="checkbox" />
      <input type="file" />
    </form>);`;
    expect(champsBruts("a.tsx", source)).toEqual([
      "a.tsx:2 <input>",
      "a.tsx:3 <select>",
      "a.tsx:4 <textarea>",
      "a.tsx:5 <input>",
    ]);
  });
});

describe("les légendes de la console", () => {
  it("passent par LEGENDE : aucune capitale CSS écrite à la main (`uppercase tracking-wide`)", () => {
    const ecrites = fichiers(RACINE)
      .filter((chemin) => !chemin.endsWith(join("components", "ui.tsx")))
      .flatMap((chemin) =>
        readFileSync(chemin, "utf8")
          .split("\n")
          .flatMap((ligne, i) => (/\buppercase\b[^"]*\btracking-wide\b|\btracking-wide\b[^"]*\buppercase\b/.test(ligne) ? [`${relative(RACINE, chemin)}:${i + 1}`] : [])),
      );
    expect(ecrites).toEqual([]);
  });
});
