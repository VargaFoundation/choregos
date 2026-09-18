import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Choregos",
  description: "Un ticket entre, une mise en production maîtrisée sort.",
};

const NAV = [
  { href: "/", label: "Projets" },
  { href: "/admin", label: "Administration" },
];

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="fr" suppressHydrationWarning>
      <body>
        <Providers>
          <div className="min-h-screen">
            <header className="border-b border-line bg-surface">
              <div className="mx-auto flex max-w-[1400px] items-center gap-6 px-4 py-3">
                <Link href="/" className="text-sm font-semibold no-underline">
                  χ Choregos
                </Link>
                <nav className="flex gap-4 text-sm text-ink-muted">
                  {NAV.map((entry) => (
                    <Link key={entry.href} href={entry.href} className="no-underline hover:text-ink">
                      {entry.label}
                    </Link>
                  ))}
                </nav>
                <span className="ml-auto text-xs text-ink-muted">
                  {process.env.NEXT_PUBLIC_API_MODE === "mock" ? "mode démo (fixtures)" : ""}
                </span>
              </div>
            </header>
            <main className="mx-auto max-w-[1400px] px-4 py-6">{children}</main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
