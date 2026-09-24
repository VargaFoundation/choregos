"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { navLinkClasses } from "@varga/design-system";
import { useSession } from "@/lib/session";

const NAV = [
  { href: "/", label: "projets" },
  { href: "/admin", label: "administration" },
];

/** La navigation d'en-tête : liens, organisation courante, et qui est connecté. */
export function TopNav() {
  const pathname = usePathname();
  const { me, orgs, org, choisirOrg, deconnecter } = useSession();
  return (
    <nav className="flex flex-1 items-center gap-8" aria-label="navigation principale">
      {NAV.map((entry) => {
        const active = entry.href === "/" ? pathname === "/" || pathname.startsWith("/p/") : pathname.startsWith(entry.href);
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
        {orgs.length > 1 ? (
          <label className="flex items-center gap-2">
            <span className="text-ink-muted">organisation</span>
            <select
              aria-label="organisation courante"
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
              se déconnecter
            </button>
          </>
        ) : (
          <Link href="/login" className="text-ink-muted hover:text-ink">
            se connecter
          </Link>
        )}
      </div>
    </nav>
  );
}
