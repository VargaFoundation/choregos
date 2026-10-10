// SPDX-License-Identifier: Apache-2.0
/**
 * Les préférences d'affichage d'une personne, gardées dans son navigateur (ADR 0043).
 *
 * Le thème se choisit — sombre, clair, ou celui du système — et s'applique AVANT la première
 * peinture : un script en tête de page (`SCRIPT_DES_PREFERENCES`, posé avec le nonce de la CSP par
 * `app/layout.tsx`) lit la préférence et pose `data-theme` sur <html>. Sans ce script, la page
 * s'afficherait d'abord en sombre puis basculerait en clair : un éclair à chaque navigation.
 *
 * React ne rend jamais `data-theme` : seul ce script et `appliquerLeTheme` l'écrivent, sinon
 * l'hydratation remettrait la valeur du serveur, qui ne connaît pas la préférence.
 *
 * La barre latérale se replie en rail (S24-03) : même stockage, même script, même règle —
 * `data-sidebar="rail"` sur <html>, posé avant la peinture, jamais rendu par React.
 */

/** La clé, sur le modèle de `choregos.org` (lib/session.tsx). */
export const CLE_DU_THEME = "choregos.theme";

export type Theme = "dark" | "light";
export type PreferenceDeTheme = Theme | "system";

export const PREFERENCES_DE_THEME: readonly PreferenceDeTheme[] = ["dark", "light", "system"];

/** Sans préférence, le sombre : celui d'une console d'opérations, et celui qui vaut sans JavaScript. */
export const THEME_PAR_DEFAUT: Theme = "dark";

export function estUnePreference(valeur: unknown): valeur is PreferenceDeTheme {
  return valeur === "dark" || valeur === "light" || valeur === "system";
}

/** Le thème qui s'affiche, pour une préférence lue (ou absente) et ce que demande le système. */
export function themeResolu(preference: string | null | undefined, systemeEnClair: boolean): Theme {
  if (preference === "light" || preference === "dark") return preference;
  if (preference === "system") return systemeEnClair ? "light" : "dark";
  return THEME_PAR_DEFAUT;
}

/** La clé de la barre latérale : « rail » la replie ; toute autre valeur, ou aucune, la laisse pleine. */
export const CLE_DE_LA_BARRE = "choregos.sidebar";

export type Barre = "full" | "rail";

/**
 * Le script d'en-tête : la MÊME règle que `themeResolu` et `lireLaBarre`, écrite pour tourner seule,
 * sans module, avant React. Un stockage refusé (navigation privée stricte) donne les défauts, sans
 * erreur. `tests/preferences.test.tsx` l'exécute pour chaque valeur stockée et le compare aux deux.
 */
export const SCRIPT_DES_PREFERENCES = `(function(){var d=document.documentElement;try{var p=localStorage.getItem(${JSON.stringify(
  CLE_DU_THEME,
)});var t=p==="light"||p==="dark"?p:p==="system"&&window.matchMedia&&matchMedia("(prefers-color-scheme: light)").matches?"light":"dark";d.dataset.theme=t;if(localStorage.getItem(${JSON.stringify(
  CLE_DE_LA_BARRE,
)})==="rail")d.dataset.sidebar="rail";}catch(e){d.dataset.theme="dark";}})();`;

function systemeEnClair(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: light)").matches
    : false;
}

/** La préférence gardée, ou rien (stockage vide, refusé, ou valeur inconnue). */
export function lirePreference(): PreferenceDeTheme | null {
  try {
    const valeur = window.localStorage.getItem(CLE_DU_THEME);
    return estUnePreference(valeur) ? valeur : null;
  } catch {
    return null;
  }
}

/** Pose sur <html> le thème que donne une préférence, et le rend. */
export function appliquerLeTheme(preference: PreferenceDeTheme | null): Theme {
  const theme = themeResolu(preference, systemeEnClair());
  document.documentElement.dataset.theme = theme;
  return theme;
}

/** Garde la préférence et l'applique tout de suite. */
export function choisirLeTheme(preference: PreferenceDeTheme): Theme {
  try {
    window.localStorage.setItem(CLE_DU_THEME, preference);
  } catch {
    // Un stockage refusé n'empêche pas d'appliquer le choix pour la page courante.
  }
  return appliquerLeTheme(preference);
}

/** La barre gardée : repliée seulement si on l'a repliée. */
export function lireLaBarre(): Barre {
  try {
    return window.localStorage.getItem(CLE_DE_LA_BARRE) === "rail" ? "rail" : "full";
  } catch {
    return "full";
  }
}

/** Pose la barre sur <html> : l'attribut n'existe que replié, la barre pleine est le défaut sans lui. */
export function appliquerLaBarre(barre: Barre): void {
  if (barre === "rail") document.documentElement.dataset.sidebar = "rail";
  else delete document.documentElement.dataset.sidebar;
}

/** Garde le choix et l'applique tout de suite. */
export function choisirLaBarre(barre: Barre): void {
  try {
    window.localStorage.setItem(CLE_DE_LA_BARRE, barre);
  } catch {
    // Un stockage refusé n'empêche pas de replier la barre pour la page courante.
  }
  appliquerLaBarre(barre);
}
