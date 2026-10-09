// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { navLinkClasses } from "@varga/design-system";
import { useQuery } from "@tanstack/react-query";
import { EditionBadge } from "@/components/edition";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useSession } from "@/lib/session";

// Les skills ont leur entrée (revue du 07/10 : « il n'y a pas de lien direct dans le menu ») et
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

/**
 * La navigation d'en-tête : liens, organisation courante, et qui est connecté.
 *
 * Elle ne tient sur une ligne qu'à partir de 1280 px (S21-03). En dessous, elle débordait : TOUTES
 * les pages de la console défilaient en largeur, à 1024 px comme sur un téléphone (1091 px de large
 * pour une fenêtre de 390, mesuré le 08/10, S23-01). Sous `xl`, elle se replie derrière un bouton
 * « menu » qui ouvre un panneau sous l'en-tête ; il se referme sur Échap et quand on change de page.
 */
export function TopNav({ demo = false }: { demo?: boolean }) {
  const pathname = usePathname();
  const { me, orgs, org, choisirOrg, deconnecter } = useSession();
  // L'édition ne change pas pendant une session : une seule lecture suffit.
  const edition = useQuery({ queryKey: ["edition"], queryFn: () => api.edition(), staleTime: Infinity, retry: false });
  const [ouvert, setOuvert] = useState(false);
  const bouton = useRef<HTMLButtonElement>(null);
  // Changer de page referme le menu — ajusté pendant le rendu, comme la session : un effet le
  // refermerait un rendu trop tard.
  const [cheminVu, setCheminVu] = useState(pathname);
  if (pathname !== cheminVu) {
    setCheminVu(pathname);
    setOuvert(false);
  }

  // Échap referme le menu d'où que soit le focus, et le rend au bouton.
  useEffect(() => {
    if (!ouvert) return;
    function surTouche(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOuvert(false);
      bouton.current?.focus();
    }
    window.addEventListener("keydown", surTouche);
    return () => window.removeEventListener("keydown", surTouche);
  }, [ouvert]);

  const liens = (empile: boolean) =>
    NAV.map((entry) => {
      const active = entreeActive(entry.href, pathname);
      return (
        <Link
          key={entry.href}
          href={entry.href}
          aria-current={active ? "page" : undefined}
          className={cn(
            navLinkClasses(active),
            "whitespace-nowrap",
            empile && "block py-3",
            empile && active && "font-medium",
          )}
        >
          {entry.label}
        </Link>
      );
    });

  const choixOrg =
    orgs.length > 1 ? (
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
    );

  const session = me ? (
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
  );

  const badgeDemo = demo && (
    <span className="whitespace-nowrap border border-line-strong px-2 py-0.5 text-xs text-ink-muted">
      demo · fixtures
    </span>
  );

  return (
    <nav className="flex flex-1 items-center gap-6" aria-label="main navigation">
      <div className="hidden items-center gap-6 xl:flex">{liens(false)}</div>
      <div className="ml-auto hidden items-center gap-4 whitespace-nowrap text-xs xl:flex">
        {edition.data && <EditionBadge edition={edition.data.edition} />}
        {choixOrg}
        {session}
        {badgeDemo}
      </div>
      <button
        ref={bouton}
        type="button"
        aria-expanded={ouvert}
        aria-controls="menu-principal"
        onClick={() => setOuvert((valeur) => !valeur)}
        className="ml-auto inline-flex min-h-11 items-center gap-2 border border-line px-3 text-sm text-ink hover:border-line-strong xl:hidden"
      >
        <svg aria-hidden viewBox="0 0 16 16" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.5">
          {ouvert ? <path d="M3 3l10 10M13 3L3 13" /> : <path d="M2 4h12M2 8h12M2 12h12" />}
        </svg>
        menu
      </button>
      {ouvert && (
        <div
          id="menu-principal"
          className="absolute inset-x-0 top-full max-h-[calc(100dvh-4rem)] overflow-y-auto border-b border-line bg-surface px-4 pb-6 shadow-sm sm:px-6 xl:hidden"
        >
          <div className="divide-y divide-line">{liens(true)}</div>
          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-3 border-t border-line pt-4 text-xs">
            {edition.data && <EditionBadge edition={edition.data.edition} />}
            {choixOrg}
            {session}
            {badgeDemo}
          </div>
        </div>
      )}
    </nav>
  );
}
