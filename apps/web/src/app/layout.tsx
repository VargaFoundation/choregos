// SPDX-License-Identifier: Apache-2.0
import type { Metadata, Viewport } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import { headers } from "next/headers";
import Link from "next/link";
import type { ReactNode } from "react";
import { OrganisationTheme, ServedThemeProvider, THEME_STYLE_ID } from "@/components/served-theme";
import { Container, Wordmark } from "@/components/ui";
import { EditionFooterLine, FooterText } from "@/components/edition";
import { Sidebar } from "@/components/shell/sidebar";
import { SCRIPT_DES_PREFERENCES } from "@/lib/preferences";
import { themeCss } from "@/lib/served-theme";
import { instanceTheme } from "@/lib/served-theme-server";
import "./globals.css";
import { Providers } from "./providers";
import { DocumentTitle } from "./document-title";
import { TopNav } from "./top-nav";

// A nonce per request (CSP, `src/proxy.ts`) requires a render per request.
export const dynamic = "force-dynamic";

// The title is set page by page (`DocumentTitle`): a title here would make a second one.
export const metadata: Metadata = {
  description: "A ticket goes in, a controlled production release comes out.",
};

// Both themes exist: the browser draws its native controls (scrollbars,
// selectors) in the page's one, which `data-theme` sets (ADR 0043).
export const viewport: Viewport = { colorScheme: "dark light" };

export default async function RootLayout({ children }: { children: ReactNode }) {
  const demo = process.env.NEXT_PUBLIC_API_MODE === "mock";
  // The theme script runs before React, so outside Next's `strict-dynamic`: it carries the request's
  // nonce (`src/proxy.ts`), without which the CSP would block it without a word.
  const requestHeaders = await headers();
  const nonce = requestHeaders.get("x-nonce") ?? undefined;
  // The embedded component (S63-12) is framed by a host application: no navigation, no footer.
  const embedded = requestHeaders.get("x-embed") === "1";
  // The look the API serves, read before anything is drawn (ADR 0064); the default on any failure.
  const theme = await instanceTheme();
  return (
    // The font variables on <html>: the theme tokens (`:root`) read them at this level.
    <html lang="en" suppressHydrationWarning
      data-default-theme={theme.default_mode}
      className={`${GeistSans.variable} ${GeistMono.variable}`}
    >
      <head>
        {/* The served theme as CSS variables, in the page itself: styles may be inline (CSP), and the
            first paint is already themed. The organisation swap rewrites this element's text. */}
        <style id={THEME_STYLE_ID} suppressHydrationWarning dangerouslySetInnerHTML={{ __html: themeCss(theme) }} />
        {theme.favicon && <link rel="icon" href={theme.favicon} />}
        {/* The chosen theme, set BEFORE the first paint: no dark flash and then light. */}
        <script nonce={nonce} suppressHydrationWarning dangerouslySetInnerHTML={{ __html: SCRIPT_DES_PREFERENCES }} />
      </head>
      <body>
        <ServedThemeProvider initial={theme}>
          <Providers>
            <OrganisationTheme />
            <DocumentTitle />
            {embedded ? (
              <main id="content" className="min-h-screen">
                {children}
              </main>
            ) : (
              <>
            {/* Nine tab stops separated the top of the page from its content (S23-05, WCAG 2.4.1). */}
            <a
              href="#content"
              className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:bg-inverse focus:px-4 focus:py-3 focus:text-sm focus:text-inverse-ink"
            >
              Skip to content
            </a>
            {/* The shell of an operations console (S24-03): from 1280 px, the sidebar on the left (collapsible to a
                rail) and the page on the right; below that, a single column. */}
            <div className="min-h-screen xl:grid xl:grid-cols-[240px_minmax(0,1fr)] xl:rail:grid-cols-[52px_minmax(0,1fr)]">
              <Sidebar />
              <div className="flex min-h-screen min-w-0 flex-col">
                {/* 48 px, border included: its rule continues the brand's, in the sidebar. */}
                <header data-top-bar className="sticky top-0 z-20 h-12 border-b border-line bg-surface/95 backdrop-blur-sm">
                  <Container size="wide" className="flex h-full items-center gap-4 sm:gap-8">
                    {/* From 1280 px, the brand sits at the top of the sidebar. */}
                    <Link href="/" className="no-underline xl:hidden">
                      <Wordmark />
                    </Link>
                    <TopNav demo={demo} />
                  </Container>
                </header>
                <main id="content" tabIndex={-1} className="flex-1 focus:outline-none">
                  {/* A page that needs the whole window (the board's columns) says so with `data-full-width`. */}
                  <Container size="wide" className="py-8 has-[[data-full-width]]:max-w-none">
                    {children}
                  </Container>
                </main>
                <footer className="border-t border-line">
                  <Container
                    size="wide"
                    className="flex min-h-12 flex-wrap items-center justify-between gap-x-6 gap-y-1 py-3 text-xs text-ink-muted"
                  >
                    <FooterText />
                    {/* The foundation is named here, and only here (ADR 0048). A theme cannot remove this
                        line; a renamed instance says what it is built on (ADR 0064). */}
                    <EditionFooterLine />
                  </Container>
                </footer>
              </div>
            </div>
              </>
            )}
          </Providers>
        </ServedThemeProvider>
      </body>
    </html>
  );
}
