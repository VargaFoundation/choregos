// SPDX-License-Identifier: Apache-2.0
"use client";

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { IS_MOCK } from "@/lib/api";
import { rememberedOrg } from "@/lib/remembered-org";
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

/** Points the page's `<link rel="icon">` at the theme's favicon, or removes it when there is none. */
function swapFavicon(favicon: string | undefined): void {
  const current = document.head.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (!favicon) {
    current?.remove();
    return;
  }
  if (current) {
    current.href = favicon;
    return;
  }
  const link = document.createElement("link");
  link.rel = "icon";
  link.href = favicon;
  document.head.append(link);
}

/**
 * When the person's organisation has a theme of its own, swap it in after hydration: one repaint,
 * the theme drawn on the server having covered the first paint (the instance's, or the remembered
 * organisation's, S42-13). Renders nothing. A failure keeps what is shown.
 *
 * Before the session knows the person's organisations (the sign-in page, a slow `/me`), the
 * organisation remembered in the browser is the one asked for, so the page never flips to another.
 */
export function OrganisationTheme() {
  const state = useContext(ServedThemeContext);
  const { org, orgs } = useSession();
  const apply = state?.apply;
  const known = (orgs ?? []).length > 0;
  // Read after hydration (an effect), never during the render: the server cannot see localStorage.
  const target = known ? org : null;
  useEffect(() => {
    if (IS_MOCK || !apply) return;
    const slug = target ?? rememberedOrg();
    if (!slug) return;
    const controller = new AbortController();
    (async () => {
      try {
        const response = await fetch(`/api/v1/theme?org=${encodeURIComponent(slug)}`, { signal: controller.signal });
        if (!response.ok) return;
        const theme = sanitizeTheme(await response.json());
        const style = document.getElementById(THEME_STYLE_ID);
        if (style) style.textContent = themeCss(theme);
        swapFavicon(theme.favicon);
        document.documentElement.dataset.defaultTheme = theme.default_mode;
        apply(theme);
        // A person without a preference follows the theme's default mode (`ThemeProvider` listens).
        window.dispatchEvent(new Event("choregos:theme"));
      } catch {
        // The theme shown stays.
      }
    })();
    return () => controller.abort();
  }, [target, apply]);
  return null;
}
