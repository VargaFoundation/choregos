// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { navLinkClasses } from "@varga/design-system";
import { useQuery } from "@tanstack/react-query";
import { EditionBadge } from "@/components/edition";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

// Les skills ont leur entrée (revue du 07/10 : « il n'y a pas de lien direct dans le menu »).
// « AI clients » plutôt qu'« integrations » : la page sert aux assistants qui entrent DANS Choregos,
// quand les connecteurs (administration) sont ce vers quoi Choregos sort. Les libellés sont courts :
// à 1280 px, l'en-tête doit tenir sur une ligne sans défiler (mesuré, S21-03).
export const NAV = [
  { href: "/", label: "projects" },
  { href: "/agents", label: "agents" },
  { href: "/skills", label: "skills" },
  { href: "/approvals", label: "approvals" },
  { href: "/integrations", label: "AI clients" },
  { href: "/admin", label: "admin" },
];

export function entreeActive(href: string, pathname: string): boolean {
  return href === "/" ? pathname === "/" || pathname.startsWith("/p/") : pathname.startsWith(href);
}

/** La navigation d'en-tête : liens, organisation courante, et qui est connecté. */
export function TopNav() {
  const pathname = usePathname();
  const { me, orgs, org, choisirOrg, deconnecter } = useSession();
  // L'édition ne change pas pendant une session : une seule lecture suffit.
  const edition = useQuery({ queryKey: ["edition"], queryFn: () => api.edition(), staleTime: Infinity, retry: false });
  return (
    <nav className="flex flex-1 items-center gap-6" aria-label="main navigation">
      {NAV.map((entry) => {
        const active = entreeActive(entry.href, pathname);
        return (
          <Link
            key={entry.href}
            href={entry.href}
            aria-current={active ? "page" : undefined}
            className={`${navLinkClasses(active)} whitespace-nowrap`}
          >
            {entry.label}
          </Link>
        );
      })}
      <div className="ml-auto flex items-center gap-4 whitespace-nowrap text-xs">
        {edition.data && (
          <span className="hidden xl:inline-flex">
            <EditionBadge edition={edition.data.edition} />
          </span>
        )}
        {orgs.length > 1 ? (
          <label className="flex items-center gap-2">
            <span className="text-ink-muted">organisation</span>
            <select
              aria-label="current organisation"
              value={org}
              onChange={(event) => choisirOrg(event.target.value)}
              className="rounded border border-line bg-surface px-2 py-1"
            >
              {orgs.map((o) => (
                <option key={o.slug} value={o.slug}>
                  {o.name}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="text-ink-muted" title="current organisation">
            org {org}
          </span>
        )}
        {me ? (
          <>
            <span title={me.email}>{me.display_name || me.email}</span>
            <button type="button" onClick={() => void deconnecter()} className="text-ink-muted hover:text-ink">
              sign out
            </button>
          </>
        ) : (
          <Link href="/login" className="text-ink-muted hover:text-ink">
            sign in
          </Link>
        )}
      </div>
    </nav>
  );
}
