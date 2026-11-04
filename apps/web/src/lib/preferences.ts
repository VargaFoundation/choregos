// SPDX-License-Identifier: Apache-2.0
/**
 * A person's display preferences, kept in their browser (ADR 0043).
 *
 * The theme is chosen (dark, light, or the system's) and applies BEFORE the first paint: a script
 * at the top of the page (`SCRIPT_DES_PREFERENCES`, set with the CSP nonce by `app/layout.tsx`)
 * reads the preference and sets `data-theme` on <html>. Without this script, the page would first
 * show dark then switch to light: a flash on every navigation.
 *
 * React never renders `data-theme`: only this script and `appliquerLeTheme` write it, otherwise
 * hydration would put back the server's value, which does not know the preference.
 *
 * The sidebar folds into a rail (S24-03): same storage, same script, same rule:
 * `data-sidebar="rail"` on <html>, set before the paint, never rendered by React.
 */

/** The key, modelled on `choregos.org` (lib/session.tsx). */
export const THEME_KEY = "choregos.theme";

export type Theme = "dark" | "light";
export type PreferenceDeTheme = Theme | "system";

export const PREFERENCES_DE_THEME: readonly PreferenceDeTheme[] = ["dark", "light", "system"];

/** Without a preference, dark: an operations console's, and the one that holds without JavaScript. */
export const DEFAULT_THEME: Theme = "dark";

export function isPreference(value: unknown): value is PreferenceDeTheme {
  return value === "dark" || value === "light" || value === "system";
}

/**
 * The mode a person without a preference gets: the served theme's `default_mode`, which the layout
 * writes on <html> as `data-default-theme` (ADR 0064); dark without it.
 */
export function defaultTheme(): Theme {
  return typeof document !== "undefined" && document.documentElement.dataset.defaultTheme === "light"
    ? "light"
    : DEFAULT_THEME;
}

/** The theme that shows, for a preference read (or absent) and what the system asks for. */
export function themeResolu(
  preference: string | null | undefined,
  systemeEnClair: boolean,
  fallback: Theme = DEFAULT_THEME,
): Theme {
  if (preference === "light" || preference === "dark") return preference;
  if (preference === "system") return systemeEnClair ? "light" : "dark";
  return fallback;
}

/** The sidebar's key: "rail" folds it; any other value, or none, leaves it full. */
export const SIDEBAR_KEY = "choregos.sidebar";

export type Barre = "full" | "rail";

/**
 * The head script: the SAME rule as `themeResolu` and `readSidebar`, written to run alone, without
 * a module, before React. A refused storage (strict private browsing) gives the defaults, without an
 * error. `tests/preferences.test.tsx` runs it for every stored value and compares it to both.
 */
export const SCRIPT_DES_PREFERENCES = `(function(){var d=document.documentElement;var f=d.dataset.defaultTheme==="light"?"light":"dark";try{var p=localStorage.getItem(${JSON.stringify(
  THEME_KEY,
)});var t=p==="light"||p==="dark"?p:p==="system"?window.matchMedia&&matchMedia("(prefers-color-scheme: light)").matches?"light":"dark":f;d.dataset.theme=t;if(localStorage.getItem(${JSON.stringify(
  SIDEBAR_KEY,
)})==="rail")d.dataset.sidebar="rail";}catch(e){d.dataset.theme=f;}})();`;

function systemeEnClair(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: light)").matches
    : false;
}

/** The preference kept, or nothing (empty or refused storage, or an unknown value). */
export function readPreference(): PreferenceDeTheme | null {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    return isPreference(value) ? value : null;
  } catch {
    return null;
  }
}

/** Sets on <html> the theme a preference gives, and returns it. */
export function appliquerLeTheme(preference: PreferenceDeTheme | null): Theme {
  const theme = themeResolu(preference, systemeEnClair(), defaultTheme());
  document.documentElement.dataset.theme = theme;
  return theme;
}

/** Keeps the preference and applies it right away. */
export function chooseTheme(preference: PreferenceDeTheme): Theme {
  try {
    window.localStorage.setItem(THEME_KEY, preference);
  } catch {
    // A refused storage does not prevent applying the choice for the current page.
  }
  return appliquerLeTheme(preference);
}

/** The sidebar kept: folded only if it was folded. */
export function readSidebar(): Barre {
  try {
    return window.localStorage.getItem(SIDEBAR_KEY) === "rail" ? "rail" : "full";
  } catch {
    return "full";
  }
}

/** Sets the sidebar on <html>: the attribute exists only when folded, the full sidebar is the default without it. */
export function appliquerLaBarre(barre: Barre): void {
  if (barre === "rail") document.documentElement.dataset.sidebar = "rail";
  else delete document.documentElement.dataset.sidebar;
}

/** Keeps the choice and applies it right away. */
export function chooseSidebar(barre: Barre): void {
  try {
    window.localStorage.setItem(SIDEBAR_KEY, barre);
  } catch {
    // A refused storage does not prevent folding the sidebar for the current page.
  }
  appliquerLaBarre(barre);
}
