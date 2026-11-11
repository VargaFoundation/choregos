// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { CircleHelp, ExternalLink } from "lucide-react";
import { usePathname } from "next/navigation";
import { createContext, useContext, type ReactNode } from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { DEFAULT_DOCS_URL, docsBase, docsSlugFor, docsUrl, type DocSlug } from "@/lib/docs";

/**
 * Links to the user documentation (S62-11). Its base URL is the instance's (`docs_url` in
 * `GET /edition`), carried by a context so a link needs no request of its own: outside the provider
 * (a component rendered alone, in a test) the public site is used.
 */
const DocsBase = createContext<string>(DEFAULT_DOCS_URL);

export function DocsProvider({ children }: { children: ReactNode }) {
  // The same read as the top bar and the About panel: one request, shared through the cache.
  const edition = useQuery({ queryKey: ["edition"], queryFn: () => api.edition(), staleTime: Infinity, retry: false });
  return <DocsBase.Provider value={docsBase(edition.data?.docs_url)}>{children}</DocsBase.Provider>;
}

/** The URL of one page of the documentation, on this instance's site. */
export function useDocsUrl(slug: DocSlug): string {
  return docsUrl(useContext(DocsBase), slug);
}

/** A link to one page of the documentation, in a new tab, saying so to a screen reader. */
export function DocsLink({
  slug,
  children = "Learn more",
  className,
  testId,
}: {
  slug: DocSlug;
  children?: ReactNode;
  className?: string;
  testId?: string;
}) {
  const href = useDocsUrl(slug);
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      data-testid={testId}
      className={cn("inline-flex items-center gap-1 text-xs", className)}
    >
      {children}
      <ExternalLink aria-hidden className="size-3" strokeWidth={1.75} />
      <span className="sr-only">, documentation, opens in a new tab</span>
    </a>
  );
}

/**
 * The page header's "Learn more": the documentation page of the screen one is on, from the single map
 * `DOCS_BY_ROUTE`. Nothing on a screen the documentation does not cover.
 */
export function LearnMore() {
  const slug = docsSlugFor(usePathname());
  if (slug === null) return null;
  return <DocsLink slug={slug} testId="learn-more" className="whitespace-nowrap" />;
}

/** The top bar's "Help": the documentation's home, on this instance's site. A link, so the keyboard reaches it. */
export function HelpEntry({ className }: { className?: string }) {
  const href = useDocsUrl("");
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      data-testid="help-entry"
      title="Documentation (opens in a new tab)"
      className={cn(
        // 28 px, like the account button beside it: the top bar holds one line at 1280 px (S21-03), and a
        // finger still gets 44 px.
        "inline-flex h-7 items-center gap-1.5 rounded px-1.5 text-xs text-ink-muted no-underline hover:text-ink pointer-coarse:min-h-11",
        className,
      )}
    >
      <CircleHelp aria-hidden className="size-4 shrink-0" strokeWidth={1.75} />
      <span>Help</span>
      <span className="sr-only">, documentation, opens in a new tab</span>
    </a>
  );
}
