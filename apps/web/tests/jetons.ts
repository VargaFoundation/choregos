/**
 * Lecture des jetons de couleur de la console, pour les tests de contraste et de provenance
 * (ADR 0043) : `src/styles/theme.css` est lu par postcss, ses `var()` et `color-mix(in srgb, …)`
 * résolus, mode par mode — le sombre est `:root`, le clair `:root` puis `:root[data-theme="light"]`.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import postcss from "postcss";

export const FEUILLE_DU_THEME = resolve(import.meta.dirname, "../src/styles/theme.css");
export const JETONS_DU_DS = resolve(import.meta.dirname, "../node_modules/@varga/design-system/css/tokens.css");

export type Mode = "dark" | "light";
export type Declarations = Map<string, string>;

/** Les déclarations de propriétés personnalisées d'un sélecteur exact, hors commentaires. */
export function declarationsDe(chemin: string, selecteur: string): Declarations {
  const racine = postcss.parse(readFileSync(chemin, "utf-8"));
  const trouvees: Declarations = new Map();
  racine.walkRules((regle) => {
    // Un sélecteur de la liste suffit : le DS écrit `:root.dark, :root[data-theme="dark"]`.
    if (!regle.selectors.some((s) => s.trim() === selecteur)) return;
    // Seulement au premier niveau (pas dans un @media) : le sombre « système » du DS est à part.
    if (regle.parent?.type !== "root") return;
    regle.walkDecls((declaration) => {
      if (declaration.prop.startsWith("--")) trouvees.set(declaration.prop, declaration.value.trim());
    });
  });
  return trouvees;
}

export function jetonsDuMode(mode: Mode): Declarations {
  const sombre = declarationsDe(FEUILLE_DU_THEME, ":root");
  if (mode === "dark") return sombre;
  return new Map([...sombre, ...declarationsDe(FEUILLE_DU_THEME, ':root[data-theme="light"]')]);
}

type Rgba = [number, number, number, number];

function depuisHex(hex: string): Rgba {
  let h = hex.replace("#", "");
  if (h.length === 3) h = h.replace(/(.)/g, "$1$1");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).concat([1]) as Rgba;
}

/** Résout une valeur de couleur : un littéral hex, `transparent`, `var(--x)`, ou `color-mix(in srgb, a p%, b)`. */
export function couleur(valeur: string, jetons: Declarations, profondeur = 0): Rgba {
  if (profondeur > 20) throw new Error(`boucle de var() : ${valeur}`);
  const v = valeur.trim();
  if (v === "transparent") return [0, 0, 0, 0];
  if (v.startsWith("#")) return depuisHex(v);
  const variable = /^var\((--[\w-]+)\)$/.exec(v);
  if (variable) {
    const cible = jetons.get(variable[1]!);
    if (cible === undefined) throw new Error(`jeton inconnu : ${variable[1]}`);
    return couleur(cible, jetons, profondeur + 1);
  }
  const melange = /^color-mix\(in srgb,\s*(.+?)\s+(\d+(?:\.\d+)?)%,\s*(.+)\)$/.exec(v);
  if (melange) {
    const a = couleur(melange[1]!, jetons, profondeur + 1);
    const b = couleur(melange[3]!, jetons, profondeur + 1);
    const p = Number(melange[2]) / 100;
    // Mélange en sRGB encodé, prémultiplié par l'alpha — ce que fait le navigateur pour `in srgb`.
    const alpha = a[3] * p + b[3] * (1 - p);
    if (alpha === 0) return [0, 0, 0, 0];
    const canal = (i: number) => (a[i]! * a[3] * p + b[i]! * b[3] * (1 - p)) / alpha;
    return [canal(0), canal(1), canal(2), alpha];
  }
  throw new Error(`couleur non résolue : ${valeur}`);
}

export function jeton(nom: string, mode: Mode): Rgba {
  const jetons = jetonsDuMode(mode);
  const valeur = jetons.get(nom);
  if (valeur === undefined) throw new Error(`jeton absent en ${mode} : ${nom}`);
  return couleur(valeur, jetons);
}

function lineaire(c: number): number {
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

export function luminance([r, g, b]: Rgba): number {
  return 0.2126 * lineaire(r) + 0.7152 * lineaire(g) + 0.0722 * lineaire(b);
}

/** Le rapport de contraste WCAG entre deux couleurs opaques. */
export function contraste(a: Rgba, b: Rgba): number {
  const [clair, sombre] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (clair + 0.05) / (sombre + 0.05);
}
