// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { navLinkClasses } from "@varga/design-system";
import { useQuery } from "@tanstack/react-query";
import { EditionBadge } from "@/components/edition";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

const NAV = [
  { href: "/", label: "projects" },
  // La bibliothèque de skills se range sous les agents : ce qu'ils savent faire.
  { href: "/agents", label: "agents", aussi: "/skills" },
  { href: "/integrations", label: "integrations" },
  { href: "/admin", label: "administration" },
];

/** La navigation d'en-tête : liens, organisation courante, et qui est connecté. */
export function TopNav() {
  const pathname = usePathname();
  const { me, orgs, org, choisirOrg, deconnecter } = useSession();
  // L'édition ne change pas pendant une session : une seule lecture suffit.
  const edition = useQuery({ queryKey: ["edition"], queryFn: () => api.edition(), staleTime: Infinity, retry: false });
  return (
    <nav className="flex flex-1 items-center gap-8" aria-label="main navigation">
      {NAV.map((entry) => {
        const active =
          entry.href === "/"
            ? pathname === "/" || pathname.startsWith("/p/")
            : pathname.startsWith(entry.href) || Boolean(entry.aussi && pathname.startsWith(entry.aussi));
        return (
          <Link
            key={entry.href}
            href={entry.href}
            aria-current={active ? "page" : undefined}
            className={navLinkClasses(active)}
          >
            {entry.label}
          </Link>
        );
      })}
      <div className="ml-auto flex items-center gap-4 text-xs">
        {edition.data && <EditionBadge edition={edition.data.edition} />}
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
          <span className="text-ink-muted">organisation {org}</span>
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
