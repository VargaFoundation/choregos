// SPDX-License-Identifier: Apache-2.0
/**
 * Le lecteur du parcours (S22-02) : le direct — l'instant est « maintenant », la carte suit l'API —,
 * ou la relecture, qui avance d'un événement à l'autre à la vitesse choisie. Un réducteur pur : les
 * tests le pilotent sans horloge, et la page ne fait que lui envoyer des tics.
 */
"use client";

import { useEffect, useReducer } from "react";

export type Vitesse = 0.5 | 1 | 2;
export type EtatDuLecteur = {
  mode: "direct" | "relecture";
  index: number;
  enLecture: boolean;
  vitesse: Vitesse;
};
export type Geste =
  | { type: "lire"; nombre: number }
  | { type: "pause" }
  | { type: "recommencer"; nombre: number }
  | { type: "tic"; nombre: number }
  | { type: "aller"; index: number; nombre: number }
  | { type: "direct" }
  | { type: "vitesse"; vitesse: Vitesse };

/** Une image par événement, à 1× : assez lent pour suivre une case, assez vif pour une vie entière. */
export const DUREE_D_UNE_IMAGE_MS = 1100;

export const DEPART: EtatDuLecteur = {
  mode: "direct",
  index: 0,
  enLecture: false,
  vitesse: 1,
};

export function reduire(etat: EtatDuLecteur, geste: Geste): EtatDuLecteur {
  switch (geste.type) {
    case "lire":
      if (geste.nombre === 0) return etat;
      // Depuis le direct, ou à la fin : la relecture repart du début.
      if (etat.mode === "direct" || etat.index >= geste.nombre - 1)
        return { ...etat, mode: "relecture", index: 0, enLecture: true };
      return { ...etat, enLecture: true };
    case "pause":
      return { ...etat, enLecture: false };
    case "recommencer":
      return geste.nombre === 0 ? etat : { ...etat, mode: "relecture", index: 0, enLecture: true };
    case "tic":
      if (!etat.enLecture) return etat;
      return etat.index + 1 >= geste.nombre
        ? { ...etat, index: Math.max(geste.nombre - 1, 0), enLecture: false }
        : { ...etat, index: etat.index + 1 };
    case "aller":
      return {
        ...etat,
        mode: "relecture",
        enLecture: false,
        index: Math.min(Math.max(geste.index, 0), Math.max(geste.nombre - 1, 0)),
      };
    case "direct":
      return { ...etat, mode: "direct", enLecture: false };
    case "vitesse":
      return { ...etat, vitesse: geste.vitesse };
  }
}

export function useLecteur(nombre: number) {
  const [etat, envoyer] = useReducer(reduire, DEPART);
  useEffect(() => {
    if (!etat.enLecture) return;
    const minuterie = setInterval(() => envoyer({ type: "tic", nombre }), DUREE_D_UNE_IMAGE_MS / etat.vitesse);
    return () => clearInterval(minuterie);
  }, [etat.enLecture, etat.vitesse, nombre]);
  return {
    ...etat,
    lire: () => envoyer({ type: "lire", nombre }),
    pause: () => envoyer({ type: "pause" }),
    recommencer: () => envoyer({ type: "recommencer", nombre }),
    allerA: (index: number) => envoyer({ type: "aller", index, nombre }),
    direct: () => envoyer({ type: "direct" }),
    choisirLaVitesse: (vitesse: Vitesse) => envoyer({ type: "vitesse", vitesse }),
  };
}
