// SPDX-License-Identifier: Apache-2.0
import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { BrandMark, Container } from "@varga/design-system";
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

export default function RootLayout({ children }: { children: ReactNode }) {
  const demo = process.env.NEXT_PUBLIC_API_MODE === "mock";
  return (
    <html lang="en" suppressHydrationWarning>
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
          <div className="flex min-h-screen flex-col">
            <header className="sticky top-0 z-20 border-b border-line bg-surface/95 backdrop-blur-sm">
              <Container size="wide" className="flex h-16 items-center gap-4 sm:gap-8">
                <Link href="/" className="no-underline">
                  <BrandMark name="choregos" product="varga foundation" />
                </Link>
                <TopNav demo={demo} />
              </Container>
            </header>
            <main id="contenu" tabIndex={-1} className="flex-1 focus:outline-none">
              <Container size="wide" className="py-10">
                {children}
              </Container>
            </main>
            <footer className="border-t border-line">
              <Container
                size="wide"
                className="flex min-h-14 flex-wrap items-center justify-between gap-x-6 gap-y-1 py-3 text-xs text-ink-muted"
              >
                <span>a ticket goes in, a controlled production release comes out.</span>
                <span>apache 2.0</span>
              </Container>
            </footer>
          </div>
        </Providers>
      </body>
    </html>
  );
}
