"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { navLinkClasses } from "@varga/design-system";

const NAV = [
  { href: "/", label: "projets" },
  { href: "/admin", label: "administration" },
];

/** La navigation d'en-tête : en minuscules, le lien courant en encre, les autres en gris. */
export function TopNav() {
  const pathname = usePathname();
  return (
    <nav className="flex items-center gap-8" aria-label="navigation principale">
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
    </nav>
  );
}
