// SPDX-License-Identifier: Apache-2.0
import type { Metadata, Viewport } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import { headers } from "next/headers";
import Link from "next/link";
import type { ReactNode } from "react";
import { OrganisationTheme, ServedThemeProvider, THEME_STYLE_ID } from "@/components/served-theme";
import { Container, Wordmark } from "@/components/ui";
import { BarreLaterale } from "@/components/coquille/barre-laterale";
import { SCRIPT_DES_PREFERENCES } from "@/lib/preferences";
import { themeCss } from "@/lib/served-theme";
import { instanceTheme } from "@/lib/served-theme-server";
import "./globals.css";
import { Providers } from "./providers";
import { TitreDuDocument } from "./titre-du-document";
import { TopNav } from "./top-nav";

// Un nonce par requête (CSP, `src/proxy.ts`) exige un rendu par requête.
export const dynamic = "force-dynamic";

// Le titre se pose page par page (`TitreDuDocument`) : un titre ici en ferait un second.
export const metadata: Metadata = {
  description: "A ticket goes in, a controlled production release comes out.",
};

// Les deux thèmes existent : le navigateur dessine ses contrôles natifs (barres de défilement,
// sélecteurs) dans celui de la page, que pose `data-theme` (ADR 0043).
export const viewport: Viewport = { colorScheme: "dark light" };

export default async function RootLayout({ children }: { children: ReactNode }) {
  const demo = process.env.NEXT_PUBLIC_API_MODE === "mock";
  // Le script du thème tourne avant React, donc hors du `strict-dynamic` de Next : il porte le nonce
  // de la requête (`src/proxy.ts`), sans quoi la CSP le bloquerait sans un mot.
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  // The look the API serves, read before anything is drawn (ADR 0064); the default on any failure.
  const theme = await instanceTheme();
  return (
    // Les variables des polices sur <html> : les jetons du thème (`:root`) les lisent à ce niveau.
    <html lang="en" suppressHydrationWarning
      data-default-theme={theme.default_mode}
      className={`${GeistSans.variable} ${GeistMono.variable}`}
    >
      <head>
        {/* The served theme as CSS variables, in the page itself: styles may be inline (CSP), and the
            first paint is already themed. The organisation swap rewrites this element's text. */}
        <style id={THEME_STYLE_ID} suppressHydrationWarning dangerouslySetInnerHTML={{ __html: themeCss(theme) }} />
        {theme.favicon && <link rel="icon" href={theme.favicon} />}
        {/* Le thème choisi, posé AVANT la première peinture : pas d'éclair sombre puis clair. */}
        <script nonce={nonce} suppressHydrationWarning dangerouslySetInnerHTML={{ __html: SCRIPT_DES_PREFERENCES }} />
      </head>
      <body>
        <ServedThemeProvider initial={theme}>
          <Providers>
            <OrganisationTheme />
            <TitreDuDocument />
            {/* Nine tab stops separated the top of the page from its content (S23-05, WCAG 2.4.1). */}
            <a
              href="#contenu"
              className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:bg-inverse focus:px-4 focus:py-3 focus:text-sm focus:text-inverse-ink"
            >
              Skip to content
            </a>
            {/* The shell of an operations console (S24-03): from 1280 px, the sidebar on the left (collapsible to a
                rail) and the page on the right; below that, a single column. */}
            <div className="min-h-screen xl:grid xl:grid-cols-[240px_minmax(0,1fr)] xl:rail:grid-cols-[52px_minmax(0,1fr)]">
              <BarreLaterale />
              <div className="flex min-h-screen min-w-0 flex-col">
                {/* 48 px, border included: its rule continues the brand's, in the sidebar. */}
                <header data-barre-du-haut className="sticky top-0 z-20 h-12 border-b border-line bg-surface/95 backdrop-blur-sm">
                  <Container size="wide" className="flex h-full items-center gap-4 sm:gap-8">
                    {/* From 1280 px, the brand sits at the top of the sidebar. */}
                    <Link href="/" className="no-underline xl:hidden">
                      <Wordmark />
                    </Link>
                    <TopNav demo={demo} />
                  </Container>
                </header>
                <main id="contenu" tabIndex={-1} className="flex-1 focus:outline-none">
                  <Container size="wide" className="py-8">
                    {children}
                  </Container>
                </main>
                <footer className="border-t border-line">
                  <Container
                    size="wide"
                    className="flex min-h-12 flex-wrap items-center justify-between gap-x-6 gap-y-1 py-3 text-xs text-ink-muted"
                  >
                    <span data-testid="footer-text">
                      {theme.footer?.text ?? "A ticket goes in, a controlled production release comes out."}
                    </span>
                    {/* The foundation is named here, and only here (ADR 0048). A theme cannot remove this
                        line; a renamed instance says what it is built on (ADR 0064). */}
                    <span data-testid="footer-foundation">
                      {theme.product_name === "Choregos" ? "Choregos" : `${theme.product_name}, built on Choregos`} by Varga
                      Foundation · Apache 2.0
                    </span>
                  </Container>
                </footer>
              </div>
            </div>
          </Providers>
        </ServedThemeProvider>
      </body>
    </html>
  );
}
