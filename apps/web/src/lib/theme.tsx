// SPDX-License-Identifier: Apache-2.0
"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useSyncExternalStore, type ReactNode } from "react";
import {
  appliquerLeTheme,
  choisirLeTheme,
  CLE_DU_THEME,
  lirePreference,
  THEME_PAR_DEFAUT,
  themeResolu,
  type PreferenceDeTheme,
  type Theme,
} from "./preferences";

type EtatDuTheme = {
  /** Ce que la personne a choisi ; le défaut tant qu'elle n'a rien choisi. */
  preference: PreferenceDeTheme;
  /** Ce qui s'affiche. */
  theme: Theme;
  choisir: (preference: PreferenceDeTheme) => void;
};

const ContexteDuTheme = createContext<EtatDuTheme | null>(null);

/** Un choix fait dans CET onglet : `storage` ne prévient que les autres. */
const EVENEMENT_LOCAL = "choregos:theme";
const REQUETE_CLAIR = "(prefers-color-scheme: light)";

function systeme(): MediaQueryList | null {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(REQUETE_CLAIR)
    : null;
}

function abonner(rappel: () => void): () => void {
  const surStockage = (evenement: StorageEvent) => {
    if (evenement.key === CLE_DU_THEME) rappel();
  };
  window.addEventListener("storage", surStockage);
  window.addEventListener(EVENEMENT_LOCAL, rappel);
  const requete = systeme();
  requete?.addEventListener("change", rappel);
  return () => {
    window.removeEventListener("storage", surStockage);
    window.removeEventListener(EVENEMENT_LOCAL, rappel);
    requete?.removeEventListener("change", rappel);
  };
}

/** L'instantané : la préférence gardée et le mode du système — une chaîne, comparable par valeur. */
function instantane(): string {
  return `${lirePreference() ?? THEME_PAR_DEFAUT}|${systeme()?.matches ? "light" : "dark"}`;
}

/** Côté serveur : la préférence n'est pas connue, le script d'en-tête a déjà posé le bon thème. */
function instantaneServeur(): string {
  return `${THEME_PAR_DEFAUT}|dark`;
}

/**
 * Le thème de la console (ADR 0043). Le script d'en-tête l'a posé avant la peinture ; ce fournisseur
 * le garde à jour : un choix dans un onglet vaut pour les autres (`storage`), et « system » suit le
 * système quand il change de mode. La préférence vit dans le navigateur, hors de React : elle se lit
 * par `useSyncExternalStore`, et le thème s'en déduit.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const etat = useSyncExternalStore(abonner, instantane, instantaneServeur);
  const [lue, mode] = etat.split("|");
  const preference = (lue ?? THEME_PAR_DEFAUT) as PreferenceDeTheme;
  const theme = themeResolu(preference, mode === "light");

  // Le DOM suit : React ne rend jamais `data-theme`, seuls le script d'en-tête et cet effet l'écrivent.
  useEffect(() => {
    appliquerLeTheme(preference);
  }, [preference, theme]);

  const choisir = useCallback((nouvelle: PreferenceDeTheme) => {
    choisirLeTheme(nouvelle);
    window.dispatchEvent(new Event(EVENEMENT_LOCAL));
  }, []);

  const valeur = useMemo(() => ({ preference, theme, choisir }), [preference, theme, choisir]);
  return <ContexteDuTheme.Provider value={valeur}>{children}</ContexteDuTheme.Provider>;
}

export function useTheme(): EtatDuTheme {
  const etat = useContext(ContexteDuTheme);
  if (etat === null) throw new Error("useTheme outside ThemeProvider");
  return etat;
}
