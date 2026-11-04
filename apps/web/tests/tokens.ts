/**
 * Reads the console's colour tokens, for the contrast and provenance tests (ADR 0043):
 * `src/styles/theme.css` is read by postcss, its `var()` and `color-mix(in srgb, ...)` resolved,
 * mode by mode. Dark is `:root`, light is `:root` then `:root[data-theme="light"]`.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import postcss from "postcss";

export const THEME_SHEET = resolve(import.meta.dirname, "../src/styles/theme.css");

export type Mode = "dark" | "light";
export type Declarations = Map<string, string>;

/** The custom-property declarations of an exact selector, comments excluded. */
export function declarationsOf(path: string, selector: string): Declarations {
  const root = postcss.parse(readFileSync(path, "utf-8"));
  const found: Declarations = new Map();
  root.walkRules((rule) => {
    // One selector of the list is enough.
    if (!rule.selectors.some((s) => s.trim() === selector)) return;
    // Top level only (not inside an @media).
    if (rule.parent?.type !== "root") return;
    rule.walkDecls((declaration) => {
      if (declaration.prop.startsWith("--")) found.set(declaration.prop, declaration.value.trim());
    });
  });
  return found;
}

export function tokensOfMode(mode: Mode): Declarations {
  const dark = declarationsOf(THEME_SHEET, ":root");
  if (mode === "dark") return dark;
  return new Map([...dark, ...declarationsOf(THEME_SHEET, ':root[data-theme="light"]')]);
}

type Rgba = [number, number, number, number];

function fromHex(hex: string): Rgba {
  let h = hex.replace("#", "");
  if (h.length === 3) h = h.replace(/(.)/g, "$1$1");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).concat([1]) as Rgba;
}

/** Resolves a colour value: a hex literal, `transparent`, `var(--x)`, or `color-mix(in srgb, a p%, b)`. */
export function resolveColour(value: string, tokens: Declarations, depth = 0): Rgba {
  if (depth > 20) throw new Error(`var() loop: ${value}`);
  const v = value.trim();
  if (v === "transparent") return [0, 0, 0, 0];
  if (v.startsWith("#")) return fromHex(v);
  const variable = /^var\((--[\w-]+)\)$/.exec(v);
  if (variable) {
    const target = tokens.get(variable[1]!);
    if (target === undefined) throw new Error(`unknown token: ${variable[1]}`);
    return resolveColour(target, tokens, depth + 1);
  }
  const mix = /^color-mix\(in srgb,\s*(.+?)\s+(\d+(?:\.\d+)?)%,\s*(.+)\)$/.exec(v);
  if (mix) {
    const a = resolveColour(mix[1]!, tokens, depth + 1);
    const b = resolveColour(mix[3]!, tokens, depth + 1);
    const p = Number(mix[2]) / 100;
    // A mix in encoded sRGB, premultiplied by alpha: what the browser does for `in srgb`.
    const alpha = a[3] * p + b[3] * (1 - p);
    if (alpha === 0) return [0, 0, 0, 0];
    const channel = (i: number) => (a[i]! * a[3] * p + b[i]! * b[3] * (1 - p)) / alpha;
    return [channel(0), channel(1), channel(2), alpha];
  }
  throw new Error(`colour not resolved: ${value}`);
}

export function token(name: string, mode: Mode): Rgba {
  const tokens = tokensOfMode(mode);
  const value = tokens.get(name);
  if (value === undefined) throw new Error(`token missing in ${mode}: ${name}`);
  return resolveColour(value, tokens);
}

function linear(c: number): number {
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

export function luminance([r, g, b]: Rgba): number {
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
}

/** The WCAG contrast ratio between two opaque colours. */
export function contrast(a: Rgba, b: Rgba): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (light + 0.05) / (dark + 0.05);
}
