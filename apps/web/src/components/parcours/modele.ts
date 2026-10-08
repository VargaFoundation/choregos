// SPDX-License-Identifier: Apache-2.0
/**
 * Le parcours d'un ticket, en modèle (S22-02) : ce que la carte animée dessine, sans React.
 *
 * La carte d'un workflow (S21-07) dit sa FORME. Ici, on suit UN ticket : chaque case est une
 * transition portée par un acteur — un agent, une personne, la plateforme, le train — et son état à
 * un instant donné se déduit de ce que l'API a rendu (`/work-items/{id}/journey`) : les déplacements
 * rattachés à leur arête, et les pas de chaque acteur. Le même calcul sert le direct (l'instant est
 * « maintenant ») et la relecture (l'instant avance d'un événement à l'autre).
 *
 * Fonctions pures, déterministes : deux lectures du même parcours donnent la même carte.
 */
import type { JourneyMove, JourneyStep, ProcessStep, WorkItemJourney } from "@/lib/types";
import {
  type AreteDuGraphe,
  cheminNominal,
  estNominale,
  type Graphe,
  libelleSecondaire,
  type NoeudDuGraphe,
} from "../workflows/carte/disposition";

/** Qui porte une étape : la forme de sa case, et ce qu'elle dit en toutes lettres. */
export type Genre = "agent" | "human" | "platform" | "train";

export type Etape = {
  /** L'identifiant de la transition. */
  id: string;
  de: string;
  vers: string;
  genre: Genre;
  acteur: string | null;
  /** Le nom écrit sur la case : « Developer », « Maintainers », « Release train ». */
  libelle: string;
  /** Ce que l'acteur fait, en quelques mots : « writes the change ». */
  action: string;
  gates: string[];
  /** Le nombre de tours permis (`on_fail.max_attempts`), quand le graphe le dit. */
  maxTours: number | null;
  /** Sur le chemin nominal (`true`) ou en détour (une reprise de revue, une réparation de CI). */
  surLeChemin: boolean;
  /** La phrase de la vue processus : ce que fait la transition, à quelles conditions. */
  phrase: string | null;
};

/** Un état hors du chemin dont on revient : la case de sa transition, d'où l'on y tombe, où l'on revient. */
export type Detour = {
  etat: string;
  etape: Etape;
  sources: string[];
  retour: string;
};

/** Une arête secondaire dessinée : un renvoi en arrière sur le chemin, ou l'entrée et la sortie d'un détour. */
export type Arc = {
  cle: string;
  /** L'état d'où part le déplacement et celui où il mène — ce que les déplacements du parcours nomment. */
  de: string;
  vers: string;
  /** La case d'où l'arc part, et celle où il arrive (une étape, ou un état d'exception). */
  depuis: string;
  jusqua: { etape: string } | { etat: string } | { exception: string };
  genre: "renvoi" | "detour" | "retour" | "exception";
  libelle: string;
};

export type Phase = { cle: string; libelle: string; etapes: string[] };

export type Modele = {
  initial: string;
  chemin: string[];
  /** Les étapes du chemin, dans l'ordre. */
  etapes: Etape[];
  detours: Detour[];
  arcs: Arc[];
  /** Les états où l'on ne tombe que par une escalade ou un défaut, et les fins hors du chemin. */
  exceptions: { id: string; display: string; terminal: boolean }[];
  phases: Phase[];
  nom: (etat: string) => string;
  /** Toutes les étapes, chemin et détours, par identifiant. */
  parId: Map<string, Etape>;
};

const ACTIONS_PAR_ROLE: Record<string, string> = {
  triage: "sizes the work item",
  refine: "writes the specification",
  specify: "writes the specification",
  plan: "plans the work",
  implement: "writes the change",
  verify: "runs the checks",
  test: "runs the checks",
  review: "reviews the change",
  address_review: "addresses the review",
  release_notes: "writes the release notes",
  fix_ci: "fixes the CI",
  verify_prod: "checks production",
  research: "gathers the options",
  architect: "writes the decision",
  document: "writes the document",
};

/** « spec_writer » → « Spec writer » ; « release-captains » → « Release captains ». */
export function enClair(nom: string): string {
  const mots = nom.replace(/[_-]+/g, " ").trim();
  return mots ? mots[0]!.toUpperCase() + mots.slice(1) : nom;
}

/** Le couloir d'un état dit qui en sort (`dsl/graph.py`) : ce qu'on lit quand l'arête ne nomme pas son acteur. */
const GENRE_DU_COULOIR: Record<string, Genre> = { agent: "agent", human: "human", train: "train", system: "platform" };

function genreDe(arete: AreteDuGraphe, couloir?: string): Genre {
  if (arete.via === "release_train" || arete.actor_type === "train") return "train";
  if (arete.actor_type === "agent") return "agent";
  if (arete.actor_type === "human") return "human";
  if (arete.actor_type) return "platform";
  return GENRE_DU_COULOIR[couloir ?? ""] ?? "platform";
}

/** `on failure (≤3)` → 3 ; une demande de changements ne dit pas sa borne dans le graphe. */
function borneDe(aretes: AreteDuGraphe[], de: string): number | null {
  for (const a of aretes) {
    if (a.from !== de || a.kind !== "retry") continue;
    const borne = /\(\s*≤\s*(\d+)\s*\)/.exec(a.label ?? "")?.[1];
    if (borne) return Number(borne);
  }
  return null;
}

function actionDe(arete: AreteDuGraphe, genre: Genre, etape: ProcessStep | undefined, nom: (e: string) => string): string {
  const texte = `${etape?.who ?? ""} ${etape?.sentence ?? ""}`;
  if (genre === "agent") return ACTIONS_PAR_ROLE[arete.role ?? ""] ?? (arete.role ? enClair(arete.role).toLowerCase() : "works");
  if (genre === "train") {
    const env = /release train to ([\w-]+)/.exec(texte)?.[1];
    return env ? `deploys to ${env}` : "deploys";
  }
  if (genre === "human") return `approves → ${nom(arete.to)}`;
  if (/opens the pull request/.test(texte)) return "opens the pull request";
  if (/merges the pull request/.test(texte)) return "merges the pull request";
  if (arete.gates?.length) return `checks ${arete.gates.map((g) => g.replace(/_/g, " ")).join(", ")}`;
  return `moves on → ${nom(arete.to)}`;
}

function etapeDe(
  arete: AreteDuGraphe,
  aretes: AreteDuGraphe[],
  process: Map<string, ProcessStep>,
  nom: (e: string) => string,
  surLeChemin: boolean,
  couloir?: string,
): Etape {
  const genre = genreDe(arete, couloir);
  const etape = process.get(arete.id ?? "");
  const libelle =
    genre === "train" ? "Release train" : genre === "platform" ? "Platform" : enClair(arete.actor ?? arete.role ?? "actor");
  return {
    id: arete.id ?? `${arete.from}->${arete.to}`,
    de: arete.from,
    vers: arete.to,
    genre,
    acteur: arete.actor ?? null,
    libelle,
    action: actionDe(arete, genre, etape, nom),
    gates: arete.gates ?? [],
    maxTours: borneDe(aretes, arete.from),
    surLeChemin,
    phrase: etape?.sentence ?? null,
  };
}

const SECONDAIRES = new Set(["reject", "retry", "escalate"]);

export function modeler(journey: Pick<WorkItemJourney, "graph" | "process" | "initial">): Modele {
  const graphe = journey.graph as Graphe;
  const noeuds = graphe.nodes as NoeudDuGraphe[];
  const aretes = (graphe.edges as AreteDuGraphe[]).filter((a) => !a.wildcard);
  const parEtat = new Map(noeuds.map((n) => [n.id, n]));
  const nom = (etat: string) => parEtat.get(etat)?.display ?? etat;
  const process = new Map((journey.process ?? []).map((p) => [p.id, p]));
  const chemin = cheminNominal(graphe, journey.initial);
  const surLeChemin = new Set(chemin);

  const etapes: Etape[] = [];
  for (let i = 0; i + 1 < chemin.length; i += 1) {
    const arete = aretes.find((a) => estNominale(a) && a.from === chemin[i] && a.to === chemin[i + 1]);
    if (arete) etapes.push(etapeDe(arete, aretes, process, nom, true, parEtat.get(arete.from)?.lane));
  }
  const parId = new Map(etapes.map((e) => [e.id, e]));
  const etapeQuiQuitte = (etat: string) => etapes.find((e) => e.de === etat);

  // Les détours : un état hors du chemin dont une transition nominale ramène SUR le chemin.
  const detours: Detour[] = [];
  for (const noeud of noeuds) {
    if (surLeChemin.has(noeud.id)) continue;
    const sortie = aretes.find((a) => estNominale(a) && a.from === noeud.id && surLeChemin.has(a.to));
    if (!sortie) continue;
    const etape = etapeDe(sortie, aretes, process, nom, false, noeud.lane);
    const sources = etapes
      .filter((e) => aretes.some((a) => a.from === e.de && a.to === noeud.id && SECONDAIRES.has(a.kind ?? "")))
      .map((e) => e.id);
    if (sources.length === 0) continue;
    detours.push({ etat: noeud.id, etape, sources, retour: sortie.to });
    parId.set(etape.id, etape);
  }
  const enDetour = new Set(detours.map((d) => d.etat));

  // Les arcs : un renvoi en arrière sur le chemin (pas sur place : une reprise dans le même état est
  // un tour de plus, pas un arc), l'entrée d'un détour, son retour.
  const arcs: Arc[] = [];
  const vus = new Set<string>();
  const ajouter = (arc: Arc) => {
    if (vus.has(arc.cle)) return;
    vus.add(arc.cle);
    arcs.push(arc);
  };
  for (const e of etapes) {
    for (const a of aretes) {
      if (a.from !== e.de || a.to === e.de || !SECONDAIRES.has(a.kind ?? "")) continue;
      if (a.kind === "escalate") continue;
      const libelle = libelleSecondaire(a);
      if (surLeChemin.has(a.to)) {
        const cible = etapeQuiQuitte(a.to);
        if (!cible || chemin.indexOf(a.to) >= chemin.indexOf(e.de)) continue;
        ajouter({
          cle: `${a.from}->${a.to}`,
          de: a.from,
          vers: a.to,
          depuis: e.id,
          jusqua: { etat: a.to },
          genre: "renvoi",
          libelle,
        });
      } else if (enDetour.has(a.to)) {
        const detour = detours.find((d) => d.etat === a.to)!;
        ajouter({
          cle: `${a.from}->${a.to}`,
          de: a.from,
          vers: a.to,
          depuis: e.id,
          jusqua: { etape: detour.etape.id },
          genre: "detour",
          libelle,
        });
      }
    }
  }
  for (const d of detours) {
    ajouter({
      cle: `${d.etat}->${d.retour}`,
      de: d.etat,
      vers: d.retour,
      depuis: d.etape.id,
      jusqua: { etat: d.retour },
      genre: "retour",
      libelle: `back to ${nom(d.retour)}`,
    });
  }

  const exceptions = noeuds
    .filter((n) => !surLeChemin.has(n.id) && !enDetour.has(n.id))
    .map((n) => ({
      id: n.id,
      display: n.display,
      terminal: Boolean(n.terminal),
    }));

  return {
    initial: chemin[0] ?? journey.initial ?? "",
    chemin,
    etapes,
    detours,
    arcs,
    exceptions,
    phases: phasesDe(etapes, parEtat),
    nom,
    parId,
  };
}

/**
 * Les phases du parcours, pour sauter d'une étape à l'autre : les étapes consécutives qui mènent à
 * une même colonne du tracker (« In progress », « In review »…). Sans colonnes, une phase par étape.
 */
function phasesDe(etapes: Etape[], parEtat: Map<string, NoeudDuGraphe>): Phase[] {
  const phases: Phase[] = [];
  for (const e of etapes) {
    const colonne = parEtat.get(e.vers)?.tracker_status ?? null;
    const derniere = phases.at(-1);
    if (colonne && derniere && derniere.libelle === colonne) {
      derniere.etapes.push(e.id);
      continue;
    }
    phases.push({
      cle: `${phases.length}`,
      libelle: colonne ?? parEtat.get(e.vers)?.display ?? e.vers,
      etapes: [e.id],
    });
  }
  return phases;
}

// ───────────────────────── l'état du parcours à un instant ─────────────────────────

export type StatutEtape = "a_venir" | "en_cours" | "attend" | "fait" | "renvoye" | "echoue";

export type EtatEtape = {
  statut: StatutEtape;
  /** Les pas de l'acteur sur cette transition, commencés avant l'instant. */
  tentatives: JourneyStep[];
  tours: number;
  verdict: string | null;
};

export type Instant = {
  t: number;
  /** L'état où se trouve le ticket. */
  etat: string;
  etapes: Map<string, EtatEtape>;
  /** Les états du chemin et des détours où le ticket est passé. */
  visites: Set<string>;
  /** Combien de fois chaque arc a été pris ; `dernier` : le déplacement le plus récent est celui-là. */
  arcs: Map<string, { fois: number; dernier: boolean }>;
  /** Le déplacement le plus récent, pour le dire à voix haute pendant la relecture. */
  dernier: JourneyMove | null;
  /** La phase où en est le ticket (son index), ou `null` s'il n'est sur aucune. */
  phase: number | null;
};

const ms = (iso: string | null | undefined) => (iso ? Date.parse(iso) : Number.NaN);
const EN_ATTENTE = new Set(["waiting", "pending_approval"]);
const EN_COURS = new Set([
  "queued",
  "running",
  "approved",
  "awaiting_evidence",
  "departing",
  "collecting",
  "deploying",
  "verifying",
  "staged",
]);
const ECHECS = new Set(["failed", "timed_out", "cancelled", "rejected", "rolled_back", "aborted"]);
const RENVOIS = new Set(["reject", "changes_requested", "retry", "escalate"]);

function estUneFin(modele: Modele, etat: string): boolean {
  return etat === modele.chemin.at(-1) || modele.exceptions.some((e) => e.id === etat && e.terminal);
}

function enCoursA(pas: JourneyStep, t: number): boolean {
  const fin = ms(pas.ended_at);
  if (!Number.isNaN(fin) && fin <= t) return false;
  // Fini après l'instant : il tournait encore. Sans fin : son statut dit s'il tourne.
  if (!Number.isNaN(fin)) return true;
  return EN_COURS.has(pas.status) || EN_ATTENTE.has(pas.status);
}

export function aLInstant(journey: Pick<WorkItemJourney, "moves" | "steps">, modele: Modele, t: number): Instant {
  const deplacements = journey.moves.filter((m) => ms(m.at) <= t);
  const dernier = deplacements.at(-1) ?? null;
  const etat = dernier?.to ?? modele.initial;
  const visites = new Set<string>([modele.initial, ...deplacements.map((m) => m.to)]);

  const etapes = new Map<string, EtatEtape>();
  for (const etape of modele.parId.values()) {
    const tentatives = journey.steps.filter((s) => s.transition_id === etape.id && ms(s.started_at) <= t);
    const passages = deplacements.filter((m) => m.transition_id === etape.id && m.kind === "nominal");
    const renvois = deplacements.filter((m) => m.transition_id === etape.id && RENVOIS.has(m.kind));
    const dernierPassage = passages.length ? ms(passages.at(-1)!.at) : -Infinity;
    const dernierRenvoi = renvois.length ? ms(renvois.at(-1)!.at) : -Infinity;
    const enCours = tentatives.filter((p) => enCoursA(p, t));
    const derniere = tentatives.at(-1);
    let statut: StatutEtape;
    if (enCours.length > 0) {
      statut =
        etape.genre === "human" || enCours.some((p) => EN_ATTENTE.has(p.status) && Number.isNaN(ms(p.ended_at)))
          ? "attend"
          : "en_cours";
    } else if (etat === etape.de && !estUneFin(modele, etat)) {
      // Le ticket est là où cette étape commence : elle est la prochaine à jouer, même si elle a
      // déjà été franchie (une revue qui renvoie fait rejouer les tests). Une transition de la
      // plateforme n'a pas de pas à elle ; un agent entre deux tentatives non plus.
      statut = etape.genre === "human" ? "attend" : "en_cours";
    } else if (dernierPassage > dernierRenvoi && passages.length > 0) {
      statut = "fait";
    } else if (renvois.length > 0) {
      statut = "renvoye";
    } else if (derniere && ECHECS.has(derniere.status)) {
      statut = "echoue";
    } else {
      statut = "a_venir";
    }
    const verdict = [...tentatives].reverse().find((p) => p.verdict)?.verdict ?? null;
    etapes.set(etape.id, {
      statut,
      tentatives,
      tours: tentatives.length,
      verdict,
    });
  }

  // Un ticket à la fin du chemin : la dernière étape est faite, rien n'est « prochain ».
  const arcs = new Map<string, { fois: number; dernier: boolean }>();
  for (const arc of modele.arcs) {
    const pris = deplacements.filter((m) => m.from === arc.de && m.to === arc.vers);
    arcs.set(arc.cle, {
      fois: pris.length,
      dernier: Boolean(dernier && dernier.from === arc.de && dernier.to === arc.vers),
    });
  }

  const phase = modele.phases.findIndex((p) =>
    p.etapes.some((id) => {
      const s = etapes.get(id)?.statut;
      return s === "en_cours" || s === "attend" || s === "renvoye" || s === "echoue";
    }),
  );
  const derniereFaite = modele.phases.findLastIndex((p) => p.etapes.some((id) => etapes.get(id)?.statut === "fait"));
  return {
    t,
    etat,
    etapes,
    visites,
    arcs,
    dernier,
    phase: phase >= 0 ? phase : derniereFaite >= 0 ? derniereFaite : null,
  };
}

/** Les instants de la relecture : chaque déplacement, chaque début et chaque fin de pas, dans l'ordre. */
export function instants(journey: Pick<WorkItemJourney, "moves" | "steps">): number[] {
  const tous = new Set<number>();
  for (const m of journey.moves) tous.add(ms(m.at));
  for (const s of journey.steps) {
    tous.add(ms(s.started_at));
    tous.add(ms(s.ended_at));
  }
  return [...tous].filter((t) => !Number.isNaN(t)).sort((a, b) => a - b);
}

/** L'instant où la relecture saute pour une phase : la première fois qu'une de ses étapes a bougé. */
export function debutDePhase(journey: Pick<WorkItemJourney, "moves" | "steps">, modele: Modele, phase: Phase): number | null {
  const etapes = new Set(phase.etapes);
  const departs = new Set(phase.etapes.map((id) => modele.parId.get(id)?.de));
  const candidats = [
    ...journey.steps.filter((s) => s.transition_id && etapes.has(s.transition_id)).map((s) => ms(s.started_at)),
    ...journey.moves.filter((m) => departs.has(m.to)).map((m) => ms(m.at)),
  ].filter((t) => !Number.isNaN(t));
  return candidats.length ? Math.min(...candidats) : null;
}

/** Ce que l'arc le plus récent raconte, pour l'annoncer pendant la relecture. */
export function raconter(modele: Modele, deplacement: JourneyMove | null): string {
  if (!deplacement) return "";
  const vers = modele.nom(deplacement.to);
  const qui = deplacement.transition_id ? modele.parId.get(deplacement.transition_id)?.libelle : undefined;
  switch (deplacement.kind) {
    case "start":
      return `The work item arrives in ${vers}.`;
    case "nominal":
      return qui ? `${qui} moved it to ${vers}.` : `It moved to ${vers}.`;
    case "changes_requested":
      return `${qui ?? "A reviewer"} asked for changes: back to ${vers}.`;
    case "reject":
      return `${qui ?? "A person"} sent it back to ${vers}.`;
    case "retry":
      return `${qui ?? "The step"} failed: back to ${vers} for another round.`;
    case "escalate":
      return `Retries ran out: ${vers}.`;
    case "default":
      return `It went to ${vers}.`;
    case "resume":
      return `A person answered: it resumes in ${vers}.`;
    default:
      return `It moved to ${vers}.`;
  }
}

/**
 * La carte d'un workflow sans ticket (S22-05) : sa structure, rien de franchi, rien en cours. Chaque
 * étape est « à venir » ; la carte la colore alors par qui la porte.
 */
export function structure(modele: Modele): Instant {
  const etapes = new Map<string, EtatEtape>();
  for (const id of modele.parId.keys()) etapes.set(id, { statut: "a_venir", tentatives: [], tours: 0, verdict: null });
  const arcs = new Map(modele.arcs.map((a) => [a.cle, { fois: 0, dernier: false }]));
  return { t: Number.NEGATIVE_INFINITY, etat: "", etapes, visites: new Set(), arcs, dernier: null, phase: null };
}
