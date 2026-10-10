// SPDX-License-Identifier: Apache-2.0
import type { StatutEtape } from "./modele";

/**
 * Les couleurs du parcours d'un ticket (ADR 0043) : celles des états d'une console d'opérations —
 * bleu ce qui tourne, ambre ce qui attend une personne, vert ce qui est fait, orangé ce qui est
 * renvoyé, rouge ce qui a échoué, ardoise ce qui n'est pas atteint. Le pas d'encre de chaque échelle :
 * un trait à 3:1 au moins sur la page comme sur une carte, dans les deux thèmes
 * (`tests/couleurs-des-cartes.test.ts`). La couleur ne porte jamais seule le sens : chaque statut se
 * dit aussi en toutes lettres, et ce qui vit porte un anneau.
 */
export const COULEUR_DU_STATUT: Record<StatutEtape, string> = {
  a_venir: "var(--choregos-neutral-solid)",
  en_cours: "var(--choregos-running-ink)",
  attend: "var(--choregos-waiting-ink)",
  fait: "var(--choregos-succeeded-ink)",
  renvoye: "var(--choregos-retrying-ink)",
  echoue: "var(--choregos-failed-ink)",
};

/** Le fond d'une case : teinté quand elle vit, celui de la page sinon. */
export function fondDuStatut(statut: StatutEtape): string {
  if (statut === "en_cours") return "var(--choregos-running-soft)";
  if (statut === "attend") return "var(--choregos-waiting-soft)";
  return "var(--varga-surface)";
}

/** Un renvoi pris (une revue qui renvoie, une reprise) : l'arc, sa pointe et son compte de tours. */
export const COULEUR_DU_RENVOI = "var(--choregos-retrying-ink)";
