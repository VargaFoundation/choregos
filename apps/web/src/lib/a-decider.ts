// SPDX-License-Identifier: Apache-2.0
/**
 * Ce qui attend une personne dans l'organisation (S23-02) : les tickets arrêtés sur une demande
 * humaine — une approbation, une question, un changement de périmètre, une tâche — et les actions
 * gouvernées en attente d'approbation (ADR 0035).
 *
 * Avant, `/approvals` ne montrait que les actions : un ticket arrêté sur « Spec to approve » ne se
 * voyait qu'en ouvrant le board de son projet (revue du 07/10, point 5 : « aucune page ne dit ce qui
 * m'attend »). L'API n'a pas de lecture à l'échelle de l'organisation : on lit les tickets projet par
 * projet, sous les MÊMES clés que le board (`["items", slug]`) — une décision prise ici rafraîchit le
 * board, et l'inverse.
 */
"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { HumanRequest, WorkItemDto } from "./types";

export interface Attente {
  item: WorkItemDto;
  demande: HumanRequest;
  /** L'échéance est passée : la demande sera relancée, puis escaladée. */
  enRetard: boolean;
}

const LIBELLES: Record<string, string> = {
  approval: "approval",
  question: "question",
  scope_change: "scope change",
  task: "task",
};

export function libelleDeDemande(kind: string): string {
  return LIBELLES[kind] ?? kind.replace(/_/g, " ");
}

/** Ce que la demande dit d'elle-même : sa question, son résumé, sinon son genre. */
export function resumeDeDemande(demande: HumanRequest): string {
  const payload = demande.payload ?? {};
  for (const cle of ["question", "summary", "title", "instructions"]) {
    const valeur = payload[cle];
    if (typeof valeur === "string" && valeur.trim()) return valeur.trim();
  }
  return libelleDeDemande(demande.kind);
}

/**
 * Les tickets qui attendent quelqu'un, dans l'ordre où les traiter : ce qui est en retard d'abord,
 * puis l'échéance la plus proche, puis ce qui attend depuis le plus longtemps. Une demande déjà
 * décidée n'attend plus personne.
 */
export function ticketsEnAttente(items: WorkItemDto[], maintenant: number = Date.now()): Attente[] {
  const echeance = (a: Attente) => (a.demande.due_at ? new Date(a.demande.due_at).getTime() : Infinity);
  return items
    .filter((item) => item.pending_request && !item.pending_request.decided_at)
    .map((item) => {
      const demande = item.pending_request as HumanRequest;
      return { item, demande, enRetard: !!demande.due_at && new Date(demande.due_at).getTime() < maintenant };
    })
    .sort(
      (a, b) =>
        Number(b.enRetard) - Number(a.enRetard) ||
        echeance(a) - echeance(b) ||
        new Date(a.demande.requested_at).getTime() - new Date(b.demande.requested_at).getTime(),
    );
}

/** Les lectures de la boîte : partagées par la page et par le compteur de l'en-tête. */
export function useADecider(org: string) {
  const projets = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org), staleTime: 30_000 });
  const slugs = (projets.data?.items ?? []).map((projet) => projet.slug);
  const tickets = useQueries({
    queries: slugs.map((slug) => ({
      queryKey: ["items", slug],
      queryFn: () => api.workItems(slug),
      staleTime: 30_000,
    })),
  });
  const actions = useQuery({
    queryKey: ["org-actions", org],
    queryFn: () => api.orgActions(org, "pending_approval"),
    staleTime: 30_000,
  });
  const lus = tickets.flatMap((lecture) => lecture.data?.items ?? []);
  const enAttente = ticketsEnAttente(lus);
  return {
    tickets: enAttente,
    actions: actions.data ?? [],
    /** Les projets dont les tickets n'ont pas pu se lire : ils ne sont pas comptés, et on le dit. */
    projetsIllisibles: slugs.filter((_, index) => tickets[index]?.error),
    erreur: projets.error ?? actions.error ?? null,
    pret: !!projets.data && tickets.every((lecture) => !lecture.isPending) && !!actions.data,
    total: enAttente.length + (actions.data?.length ?? 0),
  };
}
