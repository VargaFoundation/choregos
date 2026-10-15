// SPDX-License-Identifier: Apache-2.0
"use client";

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { IS_MOCK } from "@/lib/api";
import { useSession } from "@/lib/session";
import { DEFAULT_SERVED_THEME, sanitizeTheme, themeCss, type ServedTheme } from "@/lib/served-theme";

type State = { theme: ServedTheme; apply: (theme: ServedTheme) => void };

const ServedThemeContext = createContext<State | null>(null);

/** The id of the `<style>` the layout renders: the organisation swap rewrites its text. */
export const THEME_STYLE_ID = "choregos-theme";

/**
 * The theme the layout fetched on the server (the instance's), for everything that draws a name, a
 * logo or a title. Without a provider (a unit test, a story) the default theme.
 */
export function ServedThemeProvider({ initial, children }: { initial: ServedTheme; children: ReactNode }) {
  const [theme, apply] = useState<ServedTheme>(initial);
  const value = useMemo(() => ({ theme, apply }), [theme]);
  return <ServedThemeContext.Provider value={value}>{children}</ServedThemeContext.Provider>;
}

export function useServedTheme(): ServedTheme {
  return useContext(ServedThemeContext)?.theme ?? DEFAULT_SERVED_THEME;
}

/**
 * When the person's organisation has a theme of its own, swap it in after hydration: one repaint,
 * the instance theme having covered the first paint. Renders nothing. A failure keeps what is shown.
 */
export function OrganisationTheme() {
  const state = useContext(ServedThemeContext);
  const { org } = useSession();
  const apply = state?.apply;
  useEffect(() => {
    if (IS_MOCK || !apply || !org) return;
    const controller = new AbortController();
    (async () => {
      try {
        const response = await fetch(`/api/v1/theme?org=${encodeURIComponent(org)}`, { signal: controller.signal });
        if (!response.ok) return;
        const theme = sanitizeTheme(await response.json());
        const style = document.getElementById(THEME_STYLE_ID);
        if (style) style.textContent = themeCss(theme);
        document.documentElement.dataset.defaultTheme = theme.default_mode;
        apply(theme);
        // A person without a preference follows the theme's default mode (`ThemeProvider` listens).
        window.dispatchEvent(new Event("choregos:theme"));
      } catch {
        // The instance theme stays.
      }
    })();
    return () => controller.abort();
  }, [org, apply]);
  return null;
}
