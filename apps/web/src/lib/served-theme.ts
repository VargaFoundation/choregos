// SPDX-License-Identifier: Apache-2.0
/**
 * The theme the API serves, as CSS (S39-03, ADR 0064).
 *
 * The API has already validated what it serves (schema and contrast budget). This module is the
 * second wall: it emits only what it can prove safe — colour roles from a whitelist with a `#rrggbb`
 * value, font families from the contract's character class, asset paths under `/api/v1/` — and
 * drops everything else, so a theme can never put anything but a colour, a name or a same-origin file
 * into the page, whatever the API says.
 */
import type { Theme } from "@choregos/contracts/api";

export type ServedTheme = Theme;

export const DEFAULT_SERVED_THEME: ServedTheme = {
  id: "default",
  product_name: "Choregos",
  default_mode: "dark",
  tokens: { dark: {}, light: {} },
};

/** The colour roles a theme may override: those of the contract (`--choregos-<role>`). */
const ROLE = /^[a-z][a-z-]{1,30}$/;
const HEX = /^#[0-9a-fA-F]{6}$/;
const FAMILY = /^[A-Za-z0-9 _-]{1,40}$/;
const ASSET = /^\/api\/v1\/[A-Za-z0-9/_.-]{1,200}$/;

/** A same-origin asset path, or nothing. `..` passes the character class, so it is refused here. */
export function safeAsset(value: unknown): string | undefined {
  return typeof value === "string" && ASSET.test(value) && !value.split("/").includes("..") ? value : undefined;
}

function safeFamily(value: unknown): string | undefined {
  return typeof value === "string" && FAMILY.test(value) ? value : undefined;
}

function weightOf(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isInteger(value) && value >= 100 && value <= 900 ? value : fallback;
}

function px(value: unknown, max: number): number | undefined {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= max ? value : undefined;
}

function format(src: string): string {
  const extension = /\.([a-z0-9]+)$/.exec(src)?.[1];
  const known: Record<string, string> = { woff2: "woff2", woff: "woff", ttf: "truetype", otf: "opentype" };
  return extension && known[extension] ? ` format("${known[extension]}")` : "";
}

type Font = NonNullable<NonNullable<ServedTheme["fonts"]>["title"]>;

function fontFaces(font: Font | undefined): { rules: string[]; family?: string } {
  const family = safeFamily(font?.family);
  if (!family || !Array.isArray(font?.faces)) return { rules: [] };
  const rules: string[] = [];
  for (const face of font.faces) {
    const src = safeAsset(face?.src);
    if (!src) continue;
    const style = face.style === "italic" ? "italic" : "normal";
    rules.push(
      `@font-face{font-family:"${family}";src:url("${src}")${format(src)};font-weight:${weightOf(face.weight, 400)};font-style:${style};font-display:swap}`,
    );
  }
  return rules.length > 0 ? { rules, family } : { rules: [] };
}

function declarations(tokens: Record<string, string | undefined> | undefined): string {
  return Object.entries(tokens ?? {})
    .filter(([role, value]) => ROLE.test(role) && typeof value === "string" && HEX.test(value))
    .map(([role, value]) => `--choregos-${role}:${value}`)
    .join(";");
}

/**
 * The stylesheet of a theme. Dark overrides sit under `:root:not([data-theme="light"])` and light
 * ones under `:root[data-theme="light"]`, so a dark override never leaks into the light mode. The
 * doubled `:root` raises the specificity above `theme.css` whatever the order of the stylesheets.
 * An empty string when there is nothing to override (the default theme).
 */
export function themeCss(theme: ServedTheme): string {
  const out: string[] = [];
  const title = fontFaces(theme.fonts?.title);
  const sans = fontFaces(theme.fonts?.sans);
  out.push(...title.rules, ...sans.rules);

  const dark = declarations(theme.tokens?.dark);
  const light = declarations(theme.tokens?.light);
  if (dark) out.push(`:root:root:not([data-theme="light"]){${dark}}`);
  if (light) out.push(`:root:root[data-theme="light"]{${light}}`);

  const shared: string[] = [];
  const control = px(theme.radius?.control, 8);
  const surface = px(theme.radius?.surface, 12);
  if (control !== undefined) shared.push(`--radius-sm:${control}px`, `--radius:${control}px`);
  if (surface !== undefined) {
    for (const name of ["md", "lg", "xl", "2xl", "3xl", "4xl"]) shared.push(`--radius-${name}:${surface}px`);
  }
  if (sans.family) {
    shared.push(
      `--choregos-font-sans:"${sans.family}",var(--font-geist-sans),ui-sans-serif,system-ui,sans-serif`,
    );
  }
  if (title.family) {
    shared.push(
      `--choregos-font-title:"${title.family}",var(--choregos-font-sans)`,
      `--choregos-font-title-weight:${weightOf(theme.fonts?.title?.weight, 600)}`,
    );
  }
  if (shared.length > 0) out.push(`:root:root{${shared.join(";")}}`);
  return out.join("\n");
}

/** The theme with only what is safe: name, mode, assets. Colours and fonts are filtered by `themeCss`. */
export function sanitizeTheme(raw: unknown): ServedTheme {
  if (typeof raw !== "object" || raw === null) return DEFAULT_SERVED_THEME;
  const theme = raw as Partial<ServedTheme>;
  const name = typeof theme.product_name === "string" ? theme.product_name.trim().slice(0, 40) : "";
  if (!name || typeof theme.id !== "string" || typeof theme.tokens !== "object" || theme.tokens === null) {
    return DEFAULT_SERVED_THEME;
  }
  const footer = typeof theme.footer?.text === "string" ? theme.footer.text.slice(0, 200) : undefined;
  return {
    id: theme.id,
    product_name: name,
    default_mode: theme.default_mode === "light" ? "light" : "dark",
    tokens: { dark: theme.tokens.dark ?? {}, light: theme.tokens.light ?? {} },
    radius: theme.radius,
    fonts: theme.fonts,
    logo: safeAsset(theme.logo),
    logo_dark: safeAsset(theme.logo_dark),
    favicon: safeAsset(theme.favicon),
    ...(footer ? { footer: { text: footer } } : {}),
  };
}
