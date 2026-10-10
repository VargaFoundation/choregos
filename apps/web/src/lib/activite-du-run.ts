// SPDX-License-Identifier: Apache-2.0
/**
 * L'activité d'un run, lisible (S25-02) : ce que l'agent a appelé, dans quel état, en combien de
 * temps, et ce qu'on lui a refusé — relu du journal ACP, au-dessus duquel la console la montre.
 *
 * Un appel d'outil naît d'un `tool_call` et vit de ses `tool_call_update`, qui ne portent que ce qui
 * change ; tous partagent un `toolCallId`. Une demande de permission le cite (`toolCall.toolCallId`) :
 * un refus se voit donc sur l'appel qu'il bloque. Un appel compte une fois, quel que soit le nombre de
 * ses mises à jour ; le journal stocké et le direct se recouvrent, un numéro d'événement compte une fois.
 */
import type { RunEventDto } from "./types";

export type StatutDAppel = "pending" | "in_progress" | "completed" | "failed";

export type AppelDOutil = {
  id: string;
  titre: string;
  /** Le genre ACP : read, edit, delete, move, search, execute, think, fetch, switch_mode, other. */
  genre: string;
  statut: StatutDAppel;
  /** L'heure de sa première mise à jour, et celle où il s'est terminé (`completed` ou `failed`). */
  debut: string;
  fin?: string;
  dureeMs?: number;
  /** Les fichiers qu'il vise (`locations`). */
  chemins: string[];
  /** Le refus qui l'a bloqué, s'il y en a un. */
  refus?: string;
  /** Un sous-agent : l'outil que l'agent lance pour déléguer une tâche. */
  sousAgent: boolean;
};

export type Refus = { cible: string; raison: string; appel?: string };

export type ActiviteDuRun = { appels: AppelDOutil[]; refus: Refus[]; termines: number; sousAgents: number };

const STATUTS = new Set<StatutDAppel>(["pending", "in_progress", "completed", "failed"]);
const TERMINES = new Set<StatutDAppel>(["completed", "failed"]);

/**
 * Le nom d'outil d'un sous-agent. ACP n'en a pas la notion : un sous-agent est un appel d'outil dont
 * le nom programmatique (`name`, ou celui que Claude Code glisse dans `_meta`) dit la délégation.
 */
const SOUS_AGENTS = /^(task|agent|subagent|dispatch_agent)$/i;

type Brut = Record<string, unknown>;

function nomDOutil(update: Brut): string | undefined {
  if (typeof update.name === "string") return update.name;
  const meta = update._meta as { claudeCode?: { toolName?: unknown } } | undefined;
  return typeof meta?.claudeCode?.toolName === "string" ? meta.claudeCode.toolName : undefined;
}

/** L'activité d'un run, relue de son journal. */
export function activiteDuRun(evenements: RunEventDto[]): ActiviteDuRun {
  const parSeq = new Map<number, RunEventDto>();
  for (const evenement of evenements) parSeq.set(evenement.seq, evenement);
  const appels = new Map<string, AppelDOutil>();
  const refus: Refus[] = [];

  for (const evenement of [...parSeq.values()].sort((a, b) => a.seq - b.seq)) {
    const payload = (evenement.payload ?? {}) as Brut;
    if (evenement.type === "session/request_permission") {
      if (payload.allowed !== false) continue;
      const params = (payload.params ?? {}) as { toolCall?: { toolCallId?: unknown } };
      const id = typeof params.toolCall?.toolCallId === "string" ? params.toolCall.toolCallId : undefined;
      const raison = String(payload.reason ?? "refused");
      refus.push({ cible: String(payload.target ?? ""), raison, appel: id });
      continue;
    }
    if (evenement.type !== "session/update") continue;
    const update = (payload.update ?? payload) as Brut;
    const genre = update.sessionUpdate;
    if ((genre !== "tool_call" && genre !== "tool_call_update") || typeof update.toolCallId !== "string") continue;
    const id = update.toolCallId;
    const appel =
      appels.get(id) ??
      ({ id, titre: id, genre: "other", statut: "pending", debut: evenement.ts, chemins: [], sousAgent: false } as AppelDOutil);
    if (typeof update.title === "string" && update.title) appel.titre = update.title;
    if (typeof update.kind === "string") appel.genre = update.kind;
    if (Array.isArray(update.locations)) {
      appel.chemins = (update.locations as { path?: unknown }[]).flatMap((l) => (typeof l?.path === "string" ? [l.path] : []));
    }
    const nom = nomDOutil(update);
    if (nom && SOUS_AGENTS.test(nom)) appel.sousAgent = true;
    const statut = update.status as StatutDAppel;
    if (STATUTS.has(statut)) {
      appel.statut = statut;
      // La fin, c'est la PREMIÈRE mise à jour qui le termine : une redite ne rallonge pas l'appel.
      if (TERMINES.has(statut) && !appel.fin) {
        appel.fin = evenement.ts;
        appel.dureeMs = Math.max(0, Date.parse(evenement.ts) - Date.parse(appel.debut));
      }
    }
    appels.set(id, appel);
  }

  // Un refus se pose sur l'appel qu'il cite — après coup : la demande peut précéder l'appel au journal.
  for (const r of refus) {
    const appel = r.appel ? appels.get(r.appel) : undefined;
    if (appel && !appel.refus) appel.refus = r.raison;
  }
  const liste = [...appels.values()];
  return {
    appels: liste,
    refus,
    termines: liste.filter((a) => TERMINES.has(a.statut)).length,
    sousAgents: liste.filter((a) => a.sousAgent).length,
  };
}
