// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname } from "next/navigation";
import { createContext, useContext, useState, type ReactNode } from "react";
import { api, ApiError, DEFAULT_ORG, IS_MOCK, orgSlug, setCurrentOrg } from "./api";
import { pickOrg, rememberOrgCookie, rememberedOrg } from "./remembered-org";
import type { MeDto, Org } from "./types";

type Session = {
  me: MeDto | null;
  /**
   * The first page of the person's organisations, plus the current and the remembered one even when
   * they are further down: whoever looks up the current organisation here always finds it.
   */
  orgs: Org[];
  /** More organisations than `orgs` holds: the switcher searches the server, it never filters `orgs`. */
  moreOrgs: boolean;
  org: string;
  selectOrg: (slug: string) => void;
  chargement: boolean;
  deconnecter: () => Promise<void>;
};

const SessionContext = createContext<Session | null>(null);
const ORG_KEY = "choregos.org";
const ORG_PAGE = 50;

/**
 * One page of organisations, then, by the server's exact `slug` filter, the ones the session needs
 * that the page left out (the remembered choice, the current organisation): nothing is searched in
 * the browser over a partial list (ADR 0206).
 */
async function loadOrgs(): Promise<{ orgs: Org[]; more: boolean }> {
  const first = await api.orgs({ limit: ORG_PAGE });
  const orgs = [...first.items];
  if (first.meta.has_more) {
    for (const wanted of new Set([rememberedOrg(), orgSlug()])) {
      if (!wanted || orgs.some((o) => o.slug === wanted || o.aliases?.includes(wanted))) continue;
      orgs.push(...(await api.orgs({ slug: wanted })).items.filter((o) => !orgs.some((k) => k.slug === o.slug)));
    }
  }
  return { orgs, more: first.meta.has_more };
}

/**
 * The session and the current organisation, for the whole front end.
 *
 * Before: no page knew who was signed in (`/me` was only called on the administration), a 401
 * rendered as a raw error message, and the organisation was hard-coded. Here: `/me` once, the
 * person's organisations, a choice remembered in the browser, and the API client that follows it.
 */
export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const pathname = usePathname();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me(), retry: false });
  const [org, setOrg] = useState<string>(DEFAULT_ORG);
  const orgs = useQuery({
    queryKey: ["orgs"],
    queryFn: loadOrgs,
    enabled: !!me.data,
    retry: false,
  });
  // The chosen organisation is picked when the list arrives, adjusted during the render and not in
  // an effect (the pattern React documents for "state that depends on a prop"): an effect would set
  // the state after the fact and cause a cascading render.
  const [vues, setVues] = useState<Org[] | null>(null);
  if (orgs.data && orgs.data.orgs !== vues) {
    setVues(orgs.data.orgs);
    let voulue: string | null = null;
    try {
      voulue = window.localStorage.getItem(ORG_KEY);
    } catch {
      voulue = null;
    }
    const choisie = pickOrg(orgs.data.orgs, voulue) ?? DEFAULT_ORG;
    setOrg(choisie);
    setCurrentOrg(choisie);
    rememberOrgCookie(choisie);
  }

  function chooseOrg(slug: string) {
    setOrg(slug);
    setCurrentOrg(slug);
    rememberOrgCookie(slug);
    try {
      window.localStorage.setItem(ORG_KEY, slug);
    } catch {
      /* private browsing: the choice does not survive, and that is all */
    }
    void queryClient.invalidateQueries();
  }

  async function deconnecter() {
    if (!IS_MOCK) await api.logout();
    queryClient.clear();
    window.location.assign(new URL("/login", window.location.origin).toString());
  }

  // Without a session, `request()` has already redirected to /login; nothing else is rendered
  // meanwhile. The sign-in page itself, of course, does render, and so does the embedded component
  // (`/embed/`, S63-12): framed, it is never redirected and asks the person to sign in itself.
  const nonConnecte =
    me.error instanceof ApiError &&
    me.error.status === 401 &&
    !pathname.startsWith("/login") &&
    !pathname.startsWith("/embed/");
  return (
    <SessionContext.Provider
      value={{
        me: me.data ?? null,
        orgs: orgs.data?.orgs ?? [],
        moreOrgs: orgs.data?.more ?? false,
        org,
        selectOrg: chooseOrg,
        chargement: me.isLoading || (!!me.data && orgs.isLoading),
        deconnecter,
      }}
    >
      {nonConnecte ? null : children}
    </SessionContext.Provider>
  );
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside SessionProvider");
  return session;
}
