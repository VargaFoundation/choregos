// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname } from "next/navigation";
import { createContext, useContext, useState, type ReactNode } from "react";
import { api, ApiError, DEFAULT_ORG, IS_MOCK, setCurrentOrg } from "./api";
import { pickOrg, rememberOrgCookie } from "./remembered-org";
import type { MeDto, Org } from "./types";

type Session = {
  me: MeDto | null;
  orgs: Org[];
  org: string;
  selectOrg: (slug: string) => void;
  chargement: boolean;
  deconnecter: () => Promise<void>;
};

const SessionContext = createContext<Session | null>(null);
const ORG_KEY = "choregos.org";

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
  const orgs = useQuery({ queryKey: ["orgs"], queryFn: () => api.orgs(), enabled: !!me.data, retry: false });
  const [org, setOrg] = useState<string>(DEFAULT_ORG);
  // The chosen organisation is picked when the list arrives, adjusted during the render and not in
  // an effect (the pattern React documents for "state that depends on a prop"): an effect would set
  // the state after the fact and cause a cascading render.
  const [vues, setVues] = useState<Org[] | null>(null);
  if (orgs.data && orgs.data !== vues) {
    setVues(orgs.data);
    let voulue: string | null = null;
    try {
      voulue = window.localStorage.getItem(ORG_KEY);
    } catch {
      voulue = null;
    }
    const choisie = pickOrg(orgs.data, voulue) ?? DEFAULT_ORG;
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
  // meanwhile. The sign-in page itself, of course, does render.
  const nonConnecte = me.error instanceof ApiError && me.error.status === 401 && !pathname.startsWith("/login");
  return (
    <SessionContext.Provider
      value={{
        me: me.data ?? null,
        orgs: orgs.data ?? [],
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
