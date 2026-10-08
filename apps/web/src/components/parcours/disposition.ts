// SPDX-License-Identifier: Apache-2.0
/**
 * Où se pose chaque case du parcours (S22-02) : le chemin en serpentin — une rangée de gauche à
 * droite, la suivante de droite à gauche, reliées par un demi-tour —, les détours sous la rangée de
 * l'étape qui y envoie, les renvois en arcs au-dessus. Le nombre de colonnes suit la largeur : la
 * carte ne déborde jamais, elle s'allonge.
 *
 * Fonctions pures : la même largeur donne les mêmes coordonnées.
 */
import type { Modele } from "./modele";

export type Point = { x: number; y: number };
export type Segment = {
  /** L'état que le ticket occupe sur ce tronçon : entre la case qui y mène et celle qui en part. */
  etat: string;
  d: string;
  etiquette: Point & { ancre: "start" | "middle" | "end"; largeurMax: number };
};
export type Placement = {
  largeur: number;
  hauteur: number;
  colonnes: number;
  debut: Point;
  fin: Point;
  /** Les cases des étapes, chemin et détours. */
  etapes: Map<string, Point>;
  segments: Segment[];
  arcs: Map<string, { d: string; etiquette: Point }>;
};

export const RAYON = 13;
const HAUT = 92;
/** Sous une case : son titre, son action, son détail — sur deux lignes au plus chacun. */
const SOUS_LA_CASE = 104;
/** Au-dessus d'une rangée : la place des arcs de renvoi. */
const AU_DESSUS = 76;
const DESCENTE_DU_DETOUR = 150;

export function placer(modele: Modele, largeur: number): Placement {
  // Sur un téléphone, des marges et un pas plus serrés : deux colonnes tiennent dans 340 px.
  const etroit = largeur < 600;
  const MARGE_X = etroit ? 40 : 60;
  const PAS_MIN = etroit ? 118 : 128;
  const items = ["debut", ...modele.etapes.map((e) => e.id), "fin"];
  const utile = Math.max(largeur, 2 * PAS_MIN + 2 * MARGE_X) - 2 * MARGE_X;
  const colonnes = Math.max(2, Math.min(items.length, Math.floor(utile / PAS_MIN)));
  const pas = utile / colonnes;
  const rangeeDe = (k: number) => Math.floor(k / colonnes);
  const nombreDeRangees = rangeeDe(items.length - 1) + 1;

  // Une rangée d'où part un détour est plus haute : le détour pend dessous, avec ses libellés.
  const rangeesAvecDetour = new Set<number>();
  for (const detour of modele.detours) {
    const k = items.indexOf(detour.sources[0] ?? "");
    if (k >= 0) rangeesAvecDetour.add(rangeeDe(k));
  }
  const hauteurDe = (r: number) => SOUS_LA_CASE + AU_DESSUS + (rangeesAvecDetour.has(r) ? DESCENTE_DU_DETOUR : 0);
  const ys: number[] = [HAUT];
  for (let r = 1; r < nombreDeRangees; r += 1) ys.push(ys[r - 1]! + hauteurDe(r - 1));

  const position = (k: number): Point & { rangee: number } => {
    const r = rangeeDe(k);
    const c = k % colonnes;
    const col = r % 2 === 0 ? c : colonnes - 1 - c;
    return { x: MARGE_X + (col + 0.5) * pas, y: ys[r]!, rangee: r };
  };
  const positions = items.map((_, k) => position(k));
  const etapes = new Map<string, Point>();
  modele.etapes.forEach((e, i) => etapes.set(e.id, positions[i + 1]!));

  // Les tronçons : l'état qui sépare deux cases consécutives. Un changement de rangée est un demi-tour.
  const segments: Segment[] = [];
  const milieux = new Map<string, Point>();
  for (let k = 0; k + 1 < items.length; k += 1) {
    const a = positions[k]!;
    const b = positions[k + 1]!;
    const etat = modele.chemin[k] ?? "";
    if (a.rangee === b.rangee) {
      const sens = Math.sign(b.x - a.x) || 1;
      milieux.set(etat, { x: (a.x + b.x) / 2, y: a.y });
      segments.push({
        etat,
        d: `M ${a.x + sens * RAYON} ${a.y} L ${b.x - sens * RAYON} ${b.y}`,
        etiquette: { x: (a.x + b.x) / 2, y: a.y - 10, ancre: "middle", largeurMax: pas - 2 * RAYON - 12 },
      });
    } else {
      // Rangée paire : on tourne à droite ; impaire : à gauche.
      const s = (a.rangee % 2 === 0 ? 1 : -1) * (MARGE_X - 10) * 1.3;
      milieux.set(etat, { x: a.x + s * 0.75, y: (a.y + b.y) / 2 });
      segments.push({
        etat,
        d: `M ${a.x} ${a.y + RAYON} C ${a.x + s} ${a.y + RAYON}, ${b.x + s} ${b.y - RAYON}, ${b.x} ${b.y - RAYON}`,
        // Juste au-dessus de la rangée suivante : le milieu du demi-tour est la place des détours.
        etiquette: {
          x: b.x + s * 0.62 - Math.sign(s) * 6,
          y: b.y - 34,
          ancre: s > 0 ? "end" : "start",
          largeurMax: pas * 0.9,
        },
      });
    }
  }

  // Les détours : sous l'étape qui y envoie en premier, plus bas que ses libellés ; deux voisins se poussent.
  const poses: Point[] = [];
  for (const detour of modele.detours) {
    const sources = detour.sources.map((id) => etapes.get(id)).filter((p): p is Point => Boolean(p));
    const premiere = sources[0];
    if (!premiere) continue;
    const memeRangee = sources.filter((p) => p.y === premiere.y);
    let x = memeRangee.reduce((somme, p) => somme + p.x, 0) / memeRangee.length;
    const y = premiere.y + DESCENTE_DU_DETOUR;
    while (poses.some((p) => p.y === y && Math.abs(p.x - x) < pas * 0.9)) x += pas;
    x = Math.min(Math.max(x, MARGE_X + pas / 2), MARGE_X + utile - pas / 2);
    const point = { x, y };
    poses.push(point);
    etapes.set(detour.etape.id, point);
  }

  // Les arcs passent SOUS les libellés (qui ont un fond) : on les trace au plus court, sans les contourner.
  const arcs = new Map<string, { d: string; etiquette: Point }>();
  for (const arc of modele.arcs) {
    const depart = etapes.get(arc.depuis);
    if (!depart) continue;
    if (arc.genre === "retour") {
      // Vers le tronçon de l'état où l'on revient, par en dessous.
      const cible = "etat" in arc.jusqua ? milieux.get(arc.jusqua.etat) : undefined;
      if (!cible) continue;
      const cote = cible.x >= depart.x ? 1 : -1;
      arcs.set(arc.cle, {
        d: `M ${depart.x + cote * RAYON} ${depart.y} C ${depart.x + cote * 80} ${depart.y}, ${cible.x} ${cible.y + 110}, ${cible.x} ${cible.y + 3}`,
        etiquette: { x: (depart.x + cible.x) / 2, y: (depart.y + cible.y) / 2 },
      });
      continue;
    }
    const cibleId =
      "etape" in arc.jusqua
        ? arc.jusqua.etape
        : "etat" in arc.jusqua
          ? modele.etapes.find((e) => e.de === (arc.jusqua as { etat: string }).etat)?.id
          : undefined;
    const arrivee = cibleId ? etapes.get(cibleId) : undefined;
    if (!arrivee) continue;
    if (arc.genre === "renvoi" && depart.y === arrivee.y) {
      // Au-dessus de la rangée, d'autant plus haut que le renvoi saute de cases.
      const h = 40 + Math.min(Math.abs(depart.x - arrivee.x) / pas, 5) * 6;
      arcs.set(arc.cle, {
        d: `M ${depart.x} ${depart.y - RAYON} C ${depart.x} ${depart.y - RAYON - h}, ${arrivee.x} ${arrivee.y - RAYON - h}, ${arrivee.x} ${arrivee.y - RAYON}`,
        etiquette: { x: (depart.x + arrivee.x) / 2, y: depart.y - RAYON - h * 0.75 - 6 },
      });
    } else if (arc.genre === "renvoi") {
      arcs.set(arc.cle, {
        d: `M ${depart.x} ${depart.y - RAYON} C ${depart.x} ${depart.y - 90}, ${arrivee.x} ${arrivee.y + 120}, ${arrivee.x} ${arrivee.y + RAYON}`,
        etiquette: { x: (depart.x + arrivee.x) / 2, y: (depart.y + arrivee.y) / 2 },
      });
    } else {
      // L'entrée d'un détour : du bas de l'étape qui renvoie vers le haut de la case du détour.
      arcs.set(arc.cle, {
        d: `M ${depart.x} ${depart.y + RAYON} C ${depart.x} ${depart.y + 110}, ${arrivee.x} ${arrivee.y - 70}, ${arrivee.x} ${arrivee.y - RAYON - 2}`,
        etiquette: { x: (depart.x + arrivee.x) / 2, y: (depart.y + arrivee.y) / 2 + 4 },
      });
    }
  }

  const derniere = positions.at(-1)!;
  const basDesCases = Math.max(derniere.y, ...positions.map((p) => p.y), ...poses.map((p) => p.y));
  return {
    largeur: MARGE_X * 2 + utile,
    hauteur: basDesCases + SOUS_LA_CASE,
    colonnes,
    debut: positions[0]!,
    fin: derniere,
    etapes,
    segments,
    arcs,
  };
}

/** Tronque un libellé à la place qu'on lui laisse (une estimation : 6,4 px par caractère à 11 px). */
export function tronquer(texte: string, largeurMax: number, taille = 11): string {
  const max = Math.max(4, Math.floor(largeurMax / (taille * 0.58)));
  return texte.length <= max ? texte : `${texte.slice(0, max - 1).trimEnd()}…`;
}
