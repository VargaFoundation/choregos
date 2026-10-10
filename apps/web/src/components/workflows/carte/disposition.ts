// SPDX-License-Identifier: Apache-2.0
/**
 * La carte d'un workflow, à plat (revue du 07/10 : « pas très lisible, pourquoi ne pas le mettre à
 * plat avec des cases de couleurs différentes ») : le chemin nominal en une colonne, de haut en bas,
 * et ce qui en sort — reprises, rejets, escalades, attentes humaines — dans une colonne à côté. La
 * disposition est déterministe : deux validations du même YAML donnent la même carte.
 *
 * Fonctions pures : la mise en page ne dépend que du graphe que rend l'API (`dsl/graph.py`).
 */

export type NoeudDuGraphe = {
  id: string;
  display: string;
  kind?: string;
  lane?: string;
  terminal?: boolean;
  tracker_status?: string | null;
};

export type AreteDuGraphe = {
  id?: string;
  from: string;
  to: string;
  /** `nominal` (une transition), `reject`, `retry`, `escalate` (ses issues), `default` (depuis tout état d'agent). */
  kind?: string;
  label?: string;
  wildcard?: boolean;
  actor?: string | null;
  actor_type?: string | null;
  role?: string | null;
  gates?: string[];
  via?: string | null;
  timeout_hours?: number | null;
};

export type Graphe = { nodes: unknown[]; edges: unknown[] };

/** Qui fait avancer le ticket depuis un état — la couleur de sa case, et ce qu'elle dit en toutes lettres. */
export type GenreDActeur = "agent" | "human" | "platform" | "train" | "waiting" | "end";

const GENRES: Record<string, GenreDActeur> = {
  agent: "agent",
  human: "human",
  system: "platform",
  train: "train",
  wait: "waiting",
  terminal: "end",
};

export function genreDActeur(lane: string | undefined): GenreDActeur {
  return GENRES[lane ?? ""] ?? "platform";
}

/** Le libellé écrit sur la case : la couleur ne porte jamais seule le sens. */
export const LIBELLE_DU_GENRE: Record<GenreDActeur, string> = {
  agent: "agent",
  human: "human",
  platform: "platform",
  train: "release train",
  waiting: "waiting",
  end: "end",
};

/**
 * Les couleurs des genres, dans les jetons de la console (ADR 0043) : prune l'agent, bleu la
 * personne, ardoise la plateforme, vert le train, ambre l'attente, ardoise pâle la fin. Le `bord`
 * dessine — la barre d'une case, le contour d'une forme —, le `texte` écrit, le `fond` teinte une
 * pastille de légende ; clair et sombre suivent. `tests/carte-disposition.test.ts` les mesure : un
 * bord à 3:1 au moins, un texte à 4,5:1, sur la page comme sur une carte, dans les deux thèmes.
 */
export const COULEUR_DU_GENRE: Record<GenreDActeur, { bord: string; fond: string; texte: string }> = {
  agent: { bord: "var(--choregos-agent-ink)", fond: "var(--choregos-agent-soft)", texte: "var(--choregos-agent-ink)" },
  human: { bord: "var(--choregos-running-ink)", fond: "var(--choregos-running-soft)", texte: "var(--choregos-running-ink)" },
  platform: { bord: "var(--choregos-neutral-ink)", fond: "var(--choregos-neutral-soft)", texte: "var(--choregos-neutral-ink)" },
  train: { bord: "var(--choregos-succeeded-ink)", fond: "var(--choregos-succeeded-soft)", texte: "var(--choregos-succeeded-ink)" },
  waiting: { bord: "var(--choregos-waiting-ink)", fond: "var(--choregos-waiting-soft)", texte: "var(--choregos-waiting-ink)" },
  end: { bord: "var(--choregos-neutral-solid)", fond: "var(--varga-surface-sunken)", texte: "var(--choregos-neutral-ink)" },
};

const noeudsDe = (graphe: Graphe) => graphe.nodes as NoeudDuGraphe[];
const aretesDe = (graphe: Graphe) => graphe.edges as AreteDuGraphe[];
export const estNominale = (arete: AreteDuGraphe) => !arete.kind || arete.kind === "nominal";
const estDefaut = (arete: AreteDuGraphe) => arete.kind === "default";

/** L'identifiant d'une transition « depuis n'importe quel état d'agent » : l'API le suffixe de l'état d'où elle part. */
export function cleDeTransition(arete: AreteDuGraphe): string | undefined {
  if (!arete.id) return undefined;
  return arete.wildcard ? arete.id.slice(0, arete.id.lastIndexOf(":")) : arete.id;
}

/** Au-delà, l'énumération des chemins s'arrête : un workflow très maillé ne doit pas figer la page. */
const VISITES_MAX = 20_000;

/**
 * Le chemin nominal : la plus longue suite simple de transitions nominales depuis l'état initial
 * (sinon l'état que rien n'atteint, sinon le premier). À longueur égale, celui qui finit sur un état
 * terminal ; puis l'ordre du YAML. Les transitions « depuis tout état d'agent » n'en font pas partie.
 */
export function cheminNominal(graphe: Graphe, initial?: string | null): string[] {
  const noeuds = noeudsDe(graphe);
  if (noeuds.length === 0) return [];
  const rang = new Map(noeuds.map((n, i) => [n.id, i]));
  const terminal = new Set(noeuds.filter((n) => n.terminal).map((n) => n.id));
  const aretes = aretesDe(graphe);
  const suivants = new Map<string, string[]>();
  for (const a of aretes) {
    if (!estNominale(a) || a.wildcard || !rang.has(a.to)) continue;
    const liste = suivants.get(a.from) ?? [];
    if (!liste.includes(a.to)) liste.push(a.to);
    suivants.set(a.from, liste);
  }
  // Sans état initial déclaré : celui que rien n'atteint et d'où part une transition ; sinon le premier
  // d'où part une transition (un workflow cyclique) ; sinon le premier.
  const atteints = new Set(aretes.filter((a) => !estDefaut(a)).map((a) => a.to));
  const depart =
    initial && rang.has(initial)
      ? initial
      : (noeuds.find((n) => !atteints.has(n.id) && suivants.has(n.id))?.id ??
        noeuds.find((n) => suivants.has(n.id))?.id ??
        noeuds[0]!.id);

  let meilleur: string[] = [depart];
  const meilleurQue = (a: string[], b: string[]) => {
    if (a.length !== b.length) return a.length > b.length;
    const ta = terminal.has(a.at(-1)!);
    const tb = terminal.has(b.at(-1)!);
    if (ta !== tb) return ta;
    for (let i = 0; i < a.length; i += 1) {
      const d = (rang.get(a[i]!) ?? 0) - (rang.get(b[i]!) ?? 0);
      if (d !== 0) return d < 0;
    }
    return false;
  };
  let visites = 0;
  const chemin = [depart];
  const vus = new Set([depart]);
  const explorer = (courant: string) => {
    visites += 1;
    if (visites > VISITES_MAX) return;
    if (meilleurQue(chemin, meilleur)) meilleur = [...chemin];
    for (const suivant of suivants.get(courant) ?? []) {
      if (vus.has(suivant)) continue;
      vus.add(suivant);
      chemin.push(suivant);
      explorer(suivant);
      chemin.pop();
      vus.delete(suivant);
    }
  };
  explorer(depart);
  return meilleur;
}

export type EtatDeCote = {
  id: string;
  /** Les états d'où l'on y tombe, et comment : « Ready (on failure) ». */
  depuis: { id: string; comment: string }[];
};

export type Disposition = {
  chemin: string[];
  cotes: EtatDeCote[];
  /** L'ordre de lecture : le chemin, puis la colonne d'à côté. ↓ et ↑ le suivent. */
  ordre: string[];
  /** Les transitions depuis n'importe quel état d'agent, une fois chacune, sous leur propre identifiant. */
  jokers: { cle: string; to: string; actor?: string | null }[];
};

/** Comment une arête mène à un état de côté, en clair. */
export function commentOnYTombe(arete: AreteDuGraphe): string {
  switch (arete.kind) {
    case "reject":
      return "if rejected";
    case "retry":
      return libelleSecondaire(arete);
    case "escalate":
      return libelleSecondaire(arete);
    case "default":
      return `on ${libelleSecondaire(arete)}`;
    default:
      return arete.actor ? `by ${arete.actor}` : "next";
  }
}

/**
 * Le libellé d'une arête secondaire, en anglais. Le serveur les écrivait en français (`rejet`,
 * `échec (≤2)`…) avant S21-09 : la carte les lit dans les deux langues.
 */
export function libelleSecondaire(arete: AreteDuGraphe): string {
  const brut = (arete.label ?? "").trim();
  const borne = /\(\s*≤\s*(\d+)\s*\)/.exec(brut)?.[1];
  const traduction: Record<string, string> = {
    rejet: "if rejected",
    "échecs épuisés": "when retries run out",
    "changements demandés": "on changes requested",
    "revues épuisées": "when reviews run out",
    "budget dépassé": "budget exceeded",
    "délai dépassé": "timeout",
    abandon: "abandon",
  };
  if (traduction[brut]) return traduction[brut];
  if (/^échec/.test(brut) || /^on failure/.test(brut)) return borne ? `on failure (up to ${borne} retries)` : "on failure";
  return brut || arete.kind || "";
}

export function disposerLaCarte(graphe: Graphe, initial?: string | null): Disposition {
  const noeuds = noeudsDe(graphe);
  const aretes = aretesDe(graphe);
  const chemin = cheminNominal(graphe, initial);
  const surLeChemin = new Set(chemin);
  const position = new Map(chemin.map((id, i) => [id, i]));
  const rang = new Map(noeuds.map((n, i) => [n.id, i]));

  const cotes = noeuds
    .filter((n) => !surLeChemin.has(n.id))
    .map((n) => {
      const entrantes = aretes.filter((a) => a.to === n.id && a.from !== n.id);
      // Une issue propre (rejet, reprise, escalade) ancre l'état avant un défaut « depuis tout état d'agent ».
      const depuis = entrantes
        .filter((a) => surLeChemin.has(a.from))
        .sort(
          (a, b) =>
            Number(estDefaut(a)) - Number(estDefaut(b)) || (position.get(a.from) ?? 0) - (position.get(b.from) ?? 0),
        )
        .map((a) => ({ id: a.from, comment: commentOnYTombe(a) }));
      // Atteint seulement depuis un autre état de côté (l'abandon depuis l'intervention humaine) : on le dit aussi.
      const parUnCote = entrantes
        .filter((a) => !surLeChemin.has(a.from))
        .map((a) => ({ id: a.from, comment: commentOnYTombe(a) }));
      const vus = new Set<string>();
      const unique = [...depuis, ...parUnCote].filter((d) => (vus.has(`${d.id}|${d.comment}`) ? false : vus.add(`${d.id}|${d.comment}`)));
      return { id: n.id, depuis: unique };
    })
    // Rangés à hauteur du premier état du chemin d'où l'on y tombe ; ceux qu'on atteint par un autre
    // état de côté viennent après lui ; puis l'ordre du YAML.
    .sort((a, b) => ancrage(a) - ancrage(b) || (rang.get(a.id) ?? 0) - (rang.get(b.id) ?? 0));

  function ancrage(cote: EtatDeCote): number {
    const surChemin = cote.depuis.find((d) => surLeChemin.has(d.id));
    if (surChemin) return position.get(surChemin.id) ?? 0;
    const viaCote = cote.depuis[0];
    if (viaCote) {
      const source = noeuds.find((n) => n.id === viaCote.id);
      const parent = source ? aretes.find((a) => a.to === source.id && surLeChemin.has(a.from)) : undefined;
      return (parent ? (position.get(parent.from) ?? chemin.length) : chemin.length) + 0.5;
    }
    return chemin.length + 1;
  }

  const jokers = new Map<string, { cle: string; to: string; actor?: string | null }>();
  for (const a of aretes) {
    if (!a.wildcard) continue;
    const cle = cleDeTransition(a);
    if (cle && !jokers.has(cle)) jokers.set(cle, { cle, to: a.to, actor: a.actor });
  }
  return { chemin, cotes, ordre: [...chemin, ...cotes.map((c) => c.id)], jokers: [...jokers.values()] };
}

/** Les transitions qu'une case porte : la nominale vers l'état suivant du chemin, et les autres. */
export function sortiesDe(graphe: Graphe, id: string, suivant?: string) {
  const aretes = aretesDe(graphe).filter((a) => a.from === id && !estDefaut(a) && !a.wildcard);
  const nominales = aretes.filter(estNominale);
  const principale = nominales.find((a) => a.to === suivant) ?? (suivant ? undefined : nominales[0]);
  return {
    principale,
    autres: nominales.filter((a) => a !== principale),
    secondaires: aretes.filter((a) => !estNominale(a)),
  };
}

/** La transition derrière un choix de la carte : par son identifiant, ou par la clé d'une transition joker. */
export function areteDeLaCarte(graphe: Graphe, id: string): AreteDuGraphe | undefined {
  const aretes = aretesDe(graphe).filter((a) => !estDefaut(a));
  const directe = aretes.find((a) => a.id === id && !a.wildcard);
  if (directe) return directe;
  const joker = aretes.find((a) => a.wildcard && cleDeTransition(a) === id);
  return joker ? { ...joker, id } : undefined;
}

/** Ce qui arrive depuis n'importe quel état d'agent, dit une fois : `on question → Needs a human`. */
export function defaults(graphe: Graphe): Array<{ label: string; to: string; from: string[] }> {
  const parId = new Map(noeudsDe(graphe).map((n) => [n.id, n]));
  const groupes = new Map<string, { label: string; to: string; from: string[] }>();
  for (const a of aretesDe(graphe).filter(estDefaut)) {
    const label = libelleSecondaire(a) || "default";
    const cle = `${label}->${a.to}`;
    const entree = groupes.get(cle) ?? { label, to: parId.get(a.to)?.display ?? a.to, from: [] };
    entree.from.push(parId.get(a.from)?.display ?? a.from);
    groupes.set(cle, entree);
  }
  return [...groupes.values()];
}

/** Le parcours au clavier : où mène chaque touche depuis un état, et comment le décrire. */
export function navigation(graphe: Graphe, initial?: string | null) {
  const { ordre, chemin } = disposerLaCarte(graphe, initial);
  const parId = new Map(noeudsDe(graphe).map((n) => [n.id, n]));
  const aretes = aretesDe(graphe).filter((a) => !estDefaut(a));

  // → suit la transition nominale (celle du chemin d'abord) ; ← la remonte. Déterministe, comme la carte.
  function suivre(depuis: string, sens: "aval" | "amont"): string | null {
    const i = chemin.indexOf(depuis);
    if (i >= 0) {
      const voisin = sens === "aval" ? chemin[i + 1] : chemin[i - 1];
      if (voisin) return voisin;
    }
    const candidates = aretes.filter((a) => (sens === "aval" ? a.from : a.to) === depuis);
    const choix = candidates.find(estNominale) ?? candidates[0];
    if (!choix) return null;
    return sens === "aval" ? choix.to : choix.from;
  }

  function deplacer(depuis: string, touche: string): string | null | undefined {
    const index = ordre.indexOf(depuis);
    if (index < 0) return undefined;
    switch (touche) {
      case "ArrowRight":
        return suivre(depuis, "aval");
      case "ArrowLeft":
        return suivre(depuis, "amont");
      case "ArrowDown":
        return ordre[Math.min(index + 1, ordre.length - 1)] ?? null;
      case "ArrowUp":
        return ordre[Math.max(index - 1, 0)] ?? null;
      case "Home":
        return ordre[0] ?? null;
      case "End":
        return ordre[ordre.length - 1] ?? null;
      default:
        return undefined;
    }
  }

  function decrire(id: string): string {
    const noeud = parId.get(id);
    if (!noeud) return "";
    const genre = LIBELLE_DU_GENRE[genreDActeur(noeud.lane)];
    const tete = `${noeud.display} (${genre}${noeud.terminal ? ", terminal" : ""})`;
    const sortantes = aretes.filter((a) => a.from === id && !a.wildcard);
    if (sortantes.length === 0) return `${tete}: no outgoing transition.`;
    const etapes = sortantes.map((a) => {
      const cible = parId.get(a.to)?.display ?? a.to;
      if (!estNominale(a)) return `${cible} ${commentOnYTombe(a)}`;
      const via = [a.actor ? `by ${a.actor}` : a.via ? `by the ${a.via.replace("_", " ")}` : null, a.gates?.length ? `gates ${a.gates.join(", ")}` : null]
        .filter(Boolean)
        .join(", ");
      return via ? `${cible} (${via})` : cible;
    });
    return `${tete}: → ${etapes.join("; → ")}.`;
  }

  return { ordre, deplacer, decrire };
}
