// SPDX-License-Identifier: Apache-2.0
"use client";

import { Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ThemePicker } from "@/components/theme-picker";
import { AccountMenu, personName } from "@/components/account/account-menu";
import { Count, NavigationList } from "@/components/shell/navigation";
import { HelpEntry } from "@/components/docs-link";
import { AboutEntry, EditionBadge } from "@/components/edition";
import { Select } from "@/components/ui";
import { useADecider, useInboxCount } from "@/lib/a-decider";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

/**
 * The header: current organisation, who is signed in, and, below 1280 px, the navigation.
 *
 * Who is signed in is a menu under their name (`AccountMenu`): their account, their access tokens,
 * the theme and signing out. Signed out, the theme stays in the header.
 *
 * From 1280 px, the navigation lives in the sidebar (`components/shell`, ADR 0043) and
 * the header keeps only what concerns the session. Below that, the navigation folds behind a
 * "menu" button that opens a panel under the header (S23-01: without the fold, ALL the pages
 * scrolled sideways); it closes on Escape and when the page changes.
 */
export function TopNav({ demo = false }: { demo?: boolean }) {
  const pathname = usePathname();
  const { me, orgs, org, selectOrg, deconnecter } = useSession();
  // The edition does not change during a session: a single read is enough.
  const edition = useQuery({ queryKey: ["edition"], queryFn: () => api.edition(), staleTime: Infinity, retry: false });
  // What waits for a person is counted in the header, on every page (S23-02).
  const aDecider = useADecider(org).total;
  // The panel lists the current scope's entries: inside a project, its Inbox counts the project only.
  const scopedCount = useInboxCount(org, pathname);
  const [ouvert, setOuvert] = useState(false);
  const bouton = useRef<HTMLButtonElement>(null);
  // Changing page closes the menu, adjusted during the render like the session: an effect would
  // close it one render too late.
  const [cheminVu, setCheminVu] = useState(pathname);
  if (pathname !== cheminVu) {
    setCheminVu(pathname);
    setOuvert(false);
  }

  // Escape closes the menu wherever the focus is, and gives it back to the button.
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

  const orgChoice =
    orgs.length > 1 ? (
      <label className="flex items-center gap-2">
        <span className="text-ink-muted">Organisation</span>
        <Select
          aria-label="Current organisation"
          value={org}
          onChange={(event) => selectOrg(event.target.value)}
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

  // Below 1280 px, the panel lists the account's entries itself: a menu inside a menu would not fit.
  const panelSession = me ? (
    <>
      <span className="font-medium text-ink" title={me.email} data-verbatim>
        {personName(me)}
      </span>
      <Link href="/account" className="text-ink-muted hover:text-ink">
        My account
      </Link>
      <Link href="/account#sessions" className="text-ink-muted hover:text-ink">
        My sessions
      </Link>
      <Link href="/account#tokens" className="text-ink-muted hover:text-ink">
        My access tokens
      </Link>
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
      {/* From 1280 px: the organisation on the left, the session on the right. */}
      <div className="hidden items-center text-xs xl:flex">{orgChoice}</div>
      <div className="ml-auto hidden items-center gap-4 whitespace-nowrap text-xs xl:flex">
        {edition.data && <EditionBadge edition={edition.data.edition} publisher={edition.data.publisher} />}
        {badgeDemo}
        <HelpEntry />
        {me ? (
          <AccountMenu me={me} onSignOut={() => void deconnecter()} />
        ) : (
          <>
            <Link href="/login" className="text-ink-muted hover:text-ink">
              Sign in
            </Link>
            <ThemePicker />
          </>
        )}
      </div>
      <nav className="ml-auto flex items-center gap-2 xl:hidden" aria-label="Main navigation">
        {/* Help stays in the bar below 1280 px too: the documentation is one tab stop away on a phone. */}
        <HelpEntry className="min-h-11" />
        <button
          ref={bouton}
          type="button"
          aria-expanded={ouvert}
          aria-controls="menu-principal"
          onClick={() => setOuvert((value) => !value)}
          className="-mr-3 inline-flex min-h-11 items-center gap-2 rounded px-3 text-[13px] text-ink hover:bg-surface-muted aria-expanded:bg-surface-muted"
        >
          {ouvert ? (
            <X aria-hidden className="size-4" strokeWidth={1.75} />
          ) : (
            <Menu aria-hidden className="size-4" strokeWidth={1.75} />
          )}
          Menu
          {aDecider > 0 && <Count n={aDecider} />}
        </button>
        {ouvert && (
          <div
            id="menu-principal"
            className="raised absolute inset-x-0 top-full max-h-[calc(100dvh-3rem)] overflow-y-auto border-b border-line px-4 pb-6 sm:px-6"
          >
            <NavigationList pathname={pathname} aDecider={scopedCount} variante="panneau" />
            <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-3 border-t border-line pt-4 text-xs">
              {edition.data && <EditionBadge edition={edition.data.edition} publisher={edition.data.publisher} />}
              {orgChoice}
              {panelSession}
              {badgeDemo}
              <ThemePicker />
              <AboutEntry className="inline-flex min-h-11 items-center gap-2 rounded px-1 text-ink-muted hover:text-ink" />
            </div>
          </div>
        )}
      </nav>
    </>
  );
}
