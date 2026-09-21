import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { BrandMark, Container } from "@varga/design-system";
import "./globals.css";
import { Providers } from "./providers";
import { TopNav } from "./top-nav";

export const metadata: Metadata = {
  title: "choregos · varga foundation",
  description: "Un ticket entre, une mise en production maîtrisée sort.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  const demo = process.env.NEXT_PUBLIC_API_MODE === "mock";
  return (
    <html lang="fr" suppressHydrationWarning>
      <body>
        <Providers>
          <div className="flex min-h-screen flex-col">
            <header className="sticky top-0 z-20 border-b border-line bg-surface/95 backdrop-blur-sm">
              <Container size="wide" className="flex h-16 items-center gap-10">
                <Link href="/" className="no-underline">
                  <BrandMark name="choregos" product="varga foundation" />
                </Link>
                <TopNav />
                {demo && (
                  <span className="ml-auto border border-line-strong px-2 py-0.5 text-xs text-ink-muted">
                    mode démo · fixtures
                  </span>
                )}
              </Container>
            </header>
            <main className="flex-1">
              <Container size="wide" className="py-10">
                {children}
              </Container>
            </main>
            <footer className="border-t border-line">
              <Container size="wide" className="flex h-14 items-center justify-between text-xs text-ink-muted">
                <span>un ticket entre, une mise en production maîtrisée sort.</span>
                <span>apache 2.0</span>
              </Container>
            </footer>
          </div>
        </Providers>
      </body>
    </html>
  );
}
