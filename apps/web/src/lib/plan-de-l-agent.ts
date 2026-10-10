// SPDX-License-Identifier: Apache-2.0
/**
 * Le plan de l'agent, tel qu'il le déclare en ACP (S25-01).
 *
 * Un agent ACP publie son plan par `session/update` (`sessionUpdate: "plan"`) : la liste ENTIÈRE de
 * ses tâches, à chaque fois, que le client remplace (« The Client MUST replace the current plan
 * completely »). Le journal du run garde chaque révision ; ce module les relit, sous trois règles :
 *
 * - une tâche s'identifie par son contenu : reformulée, c'est une autre tâche — l'ancienne est
 *   retirée, la nouvelle ajoutée ; rien ne fusionne en silence ;
 * - une tâche qui disparaît du plan est RETIRÉE, jamais comptée comme faite, même si l'agent l'avait
 *   cochée avant : seul le plan courant dit ce qui est fait ;
 * - seul ce que l'agent a écrit dans un plan en fait partie : aucune tâche n'est devinée de ses
 *   messages.
 *
 * C'est la parole de l'agent, pas un constat de la plateforme.
 */
import type { RunEventDto } from "./types";

export type StatutDeTache = "pending" | "in_progress" | "completed";
export type PrioriteDeTache = "high" | "medium" | "low";

export type Tache = { contenu: string; priorite: PrioriteDeTache; statut: StatutDeTache };

export type RevisionDuPlan = { seq: number; ts: string; taches: Tache[] };

export type TacheDuPlan =
  | (Tache & { retiree: false })
  | {
      contenu: string;
      priorite: PrioriteDeTache;
      retiree: true;
      /** Le dernier statut que l'agent lui donnait. */
      avant: StatutDeTache;
      /** La révision (comptée à partir de 1) d'où elle a disparu. */
      revision: number;
    };

export type PlanDeLAgent = {
  revisions: RevisionDuPlan[];
  /** Le plan courant, dans l'ordre de l'agent, puis les tâches retirées, dans l'ordre de leur retrait. */
  taches: TacheDuPlan[];
  /** Les tâches faites du plan courant — jamais une tâche retirée. */
  faites: number;
  /** Les tâches du plan courant. */
  total: number;
};

const STATUTS = new Set<StatutDeTache>(["pending", "in_progress", "completed"]);
const PRIORITES = new Set<PrioriteDeTache>(["high", "medium", "low"]);

/** L'identité d'une tâche : son contenu, espaces normalisés. La casse compte : c'est l'agent qui écrit. */
function cle(contenu: string): string {
  return contenu.trim().replace(/\s+/g, " ");
}

/** Les tâches d'une révision, ou rien si l'événement n'est pas un plan. Un doublon garde sa première place. */
function tachesDe(evenement: RunEventDto): Tache[] | null {
  if (evenement.type !== "session/update") return null;
  const payload = (evenement.payload ?? {}) as Record<string, unknown>;
  const update = (payload.update ?? payload) as Record<string, unknown>;
  if (update.sessionUpdate !== "plan" || !Array.isArray(update.entries)) return null;
  const vues = new Set<string>();
  const taches: Tache[] = [];
  for (const brute of update.entries as Record<string, unknown>[]) {
    const contenu = typeof brute?.content === "string" ? cle(brute.content) : "";
    if (!contenu || vues.has(contenu)) continue;
    vues.add(contenu);
    const statut = brute.status as StatutDeTache;
    const priorite = brute.priority as PrioriteDeTache;
    taches.push({
      contenu,
      priorite: PRIORITES.has(priorite) ? priorite : "medium",
      statut: STATUTS.has(statut) ? statut : "pending",
    });
  }
  return taches;
}

/** Le plan de l'agent d'un run, relu de son journal ; `null` s'il n'en a publié aucun. */
export function planDeLAgent(evenements: RunEventDto[]): PlanDeLAgent | null {
  // Le journal stocké et le direct se recouvrent : un événement par numéro, dans l'ordre de l'agent.
  const parSeq = new Map<number, RunEventDto>();
  for (const evenement of evenements) parSeq.set(evenement.seq, evenement);
  const revisions: RevisionDuPlan[] = [];
  for (const evenement of [...parSeq.values()].sort((a, b) => a.seq - b.seq)) {
    const taches = tachesDe(evenement);
    if (taches) revisions.push({ seq: evenement.seq, ts: evenement.ts, taches });
  }
  const courante = revisions.at(-1);
  if (!courante) return null;

  // Chaque tâche vue, avec son dernier état et la dernière révision qui la portait.
  const vues = new Map<string, { tache: Tache; derniere: number }>();
  revisions.forEach((revision, index) => {
    for (const tache of revision.taches) vues.set(tache.contenu, { tache, derniere: index });
  });
  const presentes = new Set(courante.taches.map((t) => t.contenu));
  const retirees: TacheDuPlan[] = [...vues.values()]
    .filter(({ tache }) => !presentes.has(tache.contenu))
    .sort((a, b) => a.derniere - b.derniere)
    .map(({ tache, derniere }) => ({
      contenu: tache.contenu,
      priorite: tache.priorite,
      retiree: true,
      avant: tache.statut,
      revision: derniere + 2,
    }));
  return {
    revisions,
    taches: [...courante.taches.map((t): TacheDuPlan => ({ ...t, retiree: false })), ...retirees],
    faites: courante.taches.filter((t) => t.statut === "completed").length,
    total: courante.taches.length,
  };
}
