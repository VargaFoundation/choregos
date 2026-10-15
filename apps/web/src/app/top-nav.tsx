// SPDX-License-Identifier: Apache-2.0
"use client";

import { Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChoixDuTheme } from "@/components/choix-du-theme";
import { Compte, ListeDeNavigation } from "@/components/coquille/navigation";
import { EditionBadge } from "@/components/edition";
import { Select } from "@/components/ui";
import { useADecider, useInboxCount } from "@/lib/a-decider";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

/**
 * L'en-tête : organisation courante, qui est connecté, le thème — et, sous 1280 px, la navigation.
 *
 * À partir de 1280 px, la navigation vit dans la barre latérale (`components/coquille`, ADR 0043) et
 * l'en-tête ne garde que ce qui concerne la session. En dessous, la navigation se replie derrière un
 * bouton « menu » qui ouvre un panneau sous l'en-tête (S23-01 : sans repli, TOUTES les pages
 * défilaient en largeur) ; il se referme sur Échap et quand on change de page.
 */
export function TopNav({ demo = false }: { demo?: boolean }) {
  const pathname = usePathname();
  const { me, orgs, org, choisirOrg, deconnecter } = useSession();
  // L'édition ne change pas pendant une session : une seule lecture suffit.
  const edition = useQuery({ queryKey: ["edition"], queryFn: () => api.edition(), staleTime: Infinity, retry: false });
  // Ce qui attend une personne se compte dans l'en-tête, sur chaque page (S23-02).
  const aDecider = useADecider(org).total;
  // The panel lists the current scope's entries: inside a project, its Inbox counts the project only.
  const scopedCount = useInboxCount(org, pathname);
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

  const choixOrg =
    orgs.length > 1 ? (
      <label className="flex items-center gap-2">
        <span className="text-ink-muted">Organisation</span>
        <Select
          aria-label="Current organisation"
          value={org}
          onChange={(event) => choisirOrg(event.target.value)}
          className="w-auto h-7 font-mono"
        >
          {orgs.map((o) => (
            <option key={o.slug} value={o.slug}>
              {o.name}
            </option>
          ))}
        </Select>
      </label>
    ) : (
      <span className="font-mono text-ink-muted" title="Current organisation">
        Org {org}
      </span>
    );

  const session = me ? (
    <>
      <span title={me.email}>{me.display_name || me.email}</span>
      <button type="button" onClick={() => void deconnecter()} className="text-ink-muted hover:text-ink">
        Sign out
      </button>
    </>
  ) : (
    <Link href="/login" className="text-ink-muted hover:text-ink">
      Sign in
    </Link>
  );

  const badgeDemo = demo && (
    <span className="whitespace-nowrap rounded-full border border-waiting-line bg-waiting-soft px-2 py-0.5 text-[11px] font-medium text-waiting-ink">
      Demo · fixtures
    </span>
  );

  return (
    <>
      {/* À partir de 1280 px : l'organisation à gauche, la session à droite. */}
      <div className="hidden items-center text-xs xl:flex">{choixOrg}</div>
      <div className="ml-auto hidden items-center gap-4 whitespace-nowrap text-xs xl:flex">
        {edition.data && <EditionBadge edition={edition.data.edition} />}
        {session}
        {badgeDemo}
        <ChoixDuTheme />
      </div>
      <nav className="ml-auto flex items-center xl:hidden" aria-label="Main navigation">
        <button
          ref={bouton}
          type="button"
          aria-expanded={ouvert}
          aria-controls="menu-principal"
          onClick={() => setOuvert((valeur) => !valeur)}
          className="-mr-3 inline-flex min-h-11 items-center gap-2 rounded px-3 text-[13px] text-ink hover:bg-surface-muted aria-expanded:bg-surface-muted"
        >
          {ouvert ? (
            <X aria-hidden className="size-4" strokeWidth={1.75} />
          ) : (
            <Menu aria-hidden className="size-4" strokeWidth={1.75} />
          )}
          Menu
          {aDecider > 0 && <Compte n={aDecider} />}
        </button>
        {ouvert && (
          <div
            id="menu-principal"
            className="raised absolute inset-x-0 top-full max-h-[calc(100dvh-3rem)] overflow-y-auto border-b border-line px-4 pb-6 sm:px-6"
          >
            <ListeDeNavigation pathname={pathname} aDecider={scopedCount} variante="panneau" />
            <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-3 border-t border-line pt-4 text-xs">
              {edition.data && <EditionBadge edition={edition.data.edition} />}
              {choixOrg}
              {session}
              {badgeDemo}
              <ChoixDuTheme />
            </div>
          </div>
        )}
      </nav>
    </>
  );
}
