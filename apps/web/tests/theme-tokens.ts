/**
 * The console's colour roles, resolved from `src/styles/theme.css` (S39-03, ADR 0064).
 *
 * Node only (reads the stylesheet through postcss): used by `scripts/export-theme-defaults.mts`
 * and by the drift test, never by the browser bundle. Reuses the resolver of the contrast tests.
 */
import { couleur, jetonsDuMode, type Mode } from "./jetons.ts";

/** Custom properties of `theme.css` that are not a themable colour. */
const NOT_A_ROLE = new Set(["scroll-hint"]);
const PREFIX = "--choregos-";

function hex(value: number): string {
  return Math.round(value * 255)
    .toString(16)
    .padStart(2, "0");
}

/** `{ surface: "#111113", … }` for one mode: every opaque colour role of the stylesheet. */
export function rolesOf(mode: Mode): Record<string, string> {
  const tokens = jetonsDuMode(mode);
  const roles: Record<string, string> = {};
  for (const [name, value] of [...tokens].sort(([a], [b]) => a.localeCompare(b))) {
    if (!name.startsWith(PREFIX)) continue;
    const role = name.slice(PREFIX.length);
    if (NOT_A_ROLE.has(role)) continue;
    let rgba;
    try {
      rgba = couleur(value, tokens);
    } catch {
      continue; // fonts, easing, shadows, line-height: not colours
    }
    if (rgba[3] !== 1) continue;
    roles[role] = `#${hex(rgba[0])}${hex(rgba[1])}${hex(rgba[2])}`;
  }
  return roles;
}

export function themeDefaults(): { roles: string[]; dark: Record<string, string>; light: Record<string, string> } {
  const dark = rolesOf("dark");
  const light = rolesOf("light");
  const roles = Object.keys(dark);
  if (JSON.stringify(roles) !== JSON.stringify(Object.keys(light))) {
    throw new Error("theme.css: the dark and light modes do not define the same colour roles");
  }
  return { roles, dark, light };
}
