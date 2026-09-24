"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname } from "next/navigation";
import { createContext, useContext, useState, type ReactNode } from "react";
import { api, ApiError, DEFAULT_ORG, IS_MOCK, setCurrentOrg } from "./api";
import type { MeDto, Org } from "./types";

type Session = {
  me: MeDto | null;
  orgs: Org[];
  org: string;
  choisirOrg: (slug: string) => void;
  chargement: boolean;
  deconnecter: () => Promise<void>;
};

const SessionContext = createContext<Session | null>(null);
const CLE = "choregos.org";

/**
 * La session et l'organisation courante, pour tout le front.
 *
 * Avant : aucune page ne savait qui était connecté (`/me` n'était appelé que sur
 * l'administration), un 401 se rendait en message d'erreur brut, et l'organisation était
 * `varga`, codée en dur. Ici : `/me` une fois, les organisations de la personne, un choix
 * mémorisé dans le navigateur, et le client de l'API qui en tient compte.
 */
export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const pathname = usePathname();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me(), retry: false });
  const orgs = useQuery({ queryKey: ["orgs"], queryFn: () => api.orgs(), enabled: !!me.data, retry: false });
  const [org, setOrg] = useState<string>(DEFAULT_ORG);
  // L'organisation retenue se choisit quand la liste arrive — ajustée pendant le rendu,
  // pas dans un effet (le motif que React documente pour « un état qui dépend d'une
  // prop ») : un effet poserait l'état après coup et ferait un rendu en cascade.
  const [vues, setVues] = useState<Org[] | null>(null);
  if (orgs.data && orgs.data !== vues) {
    setVues(orgs.data);
    let voulue: string | null = null;
    try {
      voulue = window.localStorage.getItem(CLE);
    } catch {
      voulue = null;
    }
    const choisie = orgs.data.find((o) => o.slug === voulue)?.slug ?? orgs.data[0]?.slug ?? DEFAULT_ORG;
    setOrg(choisie);
    setCurrentOrg(choisie);
  }

  function choisirOrg(slug: string) {
    setOrg(slug);
    setCurrentOrg(slug);
    try {
      window.localStorage.setItem(CLE, slug);
    } catch {
      /* navigation privée : le choix ne survit pas, et c'est tout */
    }
    void queryClient.invalidateQueries();
  }

  async function deconnecter() {
    if (!IS_MOCK) await api.logout();
    queryClient.clear();
    window.location.assign(new URL("/login", window.location.origin).toString());
  }

  // Sans session, `request()` a déjà envoyé vers /login ; on ne rend rien d'autre en
  // attendant. La page de connexion elle-même, bien sûr, se rend.
  const nonConnecte = me.error instanceof ApiError && me.error.status === 401 && !pathname.startsWith("/login");
  return (
    <SessionContext.Provider
      value={{
        me: me.data ?? null,
        orgs: orgs.data ?? [],
        org,
        choisirOrg,
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
  if (!session) throw new Error("useSession hors de SessionProvider");
  return session;
}
