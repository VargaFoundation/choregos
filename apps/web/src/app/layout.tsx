// SPDX-License-Identifier: Apache-2.0
import type { Metadata, Viewport } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import { headers } from "next/headers";
import Link from "next/link";
import type { ReactNode } from "react";
import { BrandMark, Container } from "@varga/design-system";
import { BarreLaterale } from "@/components/coquille/barre-laterale";
import { SCRIPT_DES_PREFERENCES } from "@/lib/preferences";
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
  return (
    // Les variables des polices sur <html> : les jetons du thème (`:root`) les lisent à ce niveau.
    <html lang="en" suppressHydrationWarning className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <head>
        {/* Le thème choisi, posé AVANT la première peinture : pas d'éclair sombre puis clair. */}
        <script nonce={nonce} suppressHydrationWarning dangerouslySetInnerHTML={{ __html: SCRIPT_DES_PREFERENCES }} />
      </head>
      <body>
        <Providers>
          <TitreDuDocument />
          {/* Neuf arrêts de tabulation séparaient le haut de la page de son contenu (S23-05, WCAG 2.4.1). */}
          <a
            href="#contenu"
            className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:bg-inverse focus:px-4 focus:py-3 focus:text-sm focus:text-inverse-ink"
          >
            skip to content
          </a>
          {/* La coquille d'une console d'opérations (S24-03) : à partir de 1280 px, la barre latérale
              à gauche (repliable en rail) et la page à droite ; en dessous, une seule colonne. */}
          <div className="min-h-screen xl:grid xl:grid-cols-[240px_minmax(0,1fr)] xl:rail:grid-cols-[52px_minmax(0,1fr)]">
            <BarreLaterale />
            <div className="flex min-h-screen min-w-0 flex-col">
              {/* 48 px bordure comprise : son filet prolonge celui de la marque, dans la barre latérale. */}
              <header className="sticky top-0 z-20 h-12 border-b border-line bg-surface/95 backdrop-blur-sm">
                <Container size="wide" className="flex h-full items-center gap-4 sm:gap-8">
                  {/* À partir de 1280 px, la marque est en tête de la barre latérale. */}
                  <Link href="/" className="no-underline xl:hidden">
                    <BrandMark name="choregos" product="varga foundation" />
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
                  <span>A ticket goes in, a controlled production release comes out.</span>
                  <span>apache 2.0</span>
                </Container>
              </footer>
            </div>
          </div>
        </Providers>
      </body>
    </html>
  );
}
