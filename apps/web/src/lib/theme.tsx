// SPDX-License-Identifier: Apache-2.0
"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useSyncExternalStore, type ReactNode } from "react";
import {
  appliquerLeTheme,
  chooseTheme,
  DEFAULT_THEME,
  defaultTheme,
  readPreference,
  themeResolu,
  THEME_KEY,
  type PreferenceDeTheme,
  type Theme,
} from "./preferences";

type ThemeState = {
  /** What the person chose; the default as long as they chose nothing. */
  preference: PreferenceDeTheme;
  /** What is shown. */
  theme: Theme;
  select: (preference: PreferenceDeTheme) => void;
};

const ThemeContext = createContext<ThemeState | null>(null);

/** A choice made in THIS tab: `storage` only warns the others. */
const LOCAL_EVENT = "choregos:theme";
const LIGHT_QUERY = "(prefers-color-scheme: light)";

function systemQuery(): MediaQueryList | null {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(LIGHT_QUERY)
    : null;
}

function subscribe(callback: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key === THEME_KEY) callback();
  };
  window.addEventListener("storage", onStorage);
  window.addEventListener(LOCAL_EVENT, callback);
  const query = systemQuery();
  query?.addEventListener("change", callback);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(LOCAL_EVENT, callback);
    query?.removeEventListener("change", callback);
  };
}

/** The snapshot: the preference kept and the system's mode, one string, comparable by value. */
function snapshot(): string {
  return `${readPreference() ?? defaultTheme()}|${systemQuery()?.matches ? "light" : "dark"}`;
}

/** Server side: the preference is not known, the head script has already set the right theme. */
function serverSnapshot(): string {
  return `${DEFAULT_THEME}|dark`;
}

/**
 * The console's theme (ADR 0043). The head script set it before the paint; this provider keeps it
 * up to date: a choice in one tab holds for the others (`storage`), and "system" follows the system
 * when it changes mode. The preference lives in the browser, outside React: it is read through
 * `useSyncExternalStore`, and the theme is deduced from it.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const state = useSyncExternalStore(subscribe, snapshot, serverSnapshot);
  const [stored, mode] = state.split("|");
  const preference = (stored ?? DEFAULT_THEME) as PreferenceDeTheme;
  const theme = themeResolu(preference, mode === "light", defaultTheme());

  // The DOM follows: React never renders `data-theme`, only the head script and this effect write it.
  useEffect(() => {
    appliquerLeTheme(preference);
  }, [preference, theme]);

  const choose = useCallback((next: PreferenceDeTheme) => {
    chooseTheme(next);
    window.dispatchEvent(new Event(LOCAL_EVENT));
  }, []);

  const value = useMemo(() => ({ preference, theme, select: choose }), [preference, theme, choose]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeState {
  const state = useContext(ThemeContext);
  if (state === null) throw new Error("useTheme outside ThemeProvider");
  return state;
}
