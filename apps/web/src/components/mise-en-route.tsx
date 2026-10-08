// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { CheckSquare, Dot } from "@varga/design-system";
import { Card } from "@/components/ui";
import { api } from "@/lib/api";
import { libelleDeSorte } from "@/lib/sortes-de-connecteurs";
import type { ConnectorDto, ProjectDto, ProjectRequirement, WorkflowSummary, WorkItemDto } from "@/lib/types";

export type EtatDEtape = "fait" | "a_verifier" | "a_faire";

export interface Etape {
  id: string;
  titre: string;
  etat: EtatDEtape;
  detail: string;
  lien?: { href: string; label: string };
}

/**
 * Ce qu'il faut à un projet pour tourner, dans l'ordre où le faire (S23-03). La revue du 07/10
 * (point 4) : « un projet neuf ne tourne pas tant que ses connecteurs et ses groupes humains
 * n'existent pas, et rien ne le dit — alors que `GET /projects/{id}/requirements` le calcule déjà ».
 *
 * Une capacité qu'aucun connecteur du projet ne couvre mais que la plateforme couvre par défaut est
 * faite : c'est ce que la plateforme promet. Un connecteur jamais testé est à vérifier ; un connecteur
 * dont le dernier test a échoué est à faire, qu'un workflow l'exige ou non. Les groupes humains ne
 * sont pas vérifiés : l'API ne les connaît pas encore.
 */
export function etapesDeMiseEnRoute({
  slug,
  projet,
  exigences,
  connecteurs,
  workflows,
  tickets,
}: {
  slug: string;
  projet: Pick<ProjectDto, "status">;
  exigences: ProjectRequirement[];
  connecteurs: ConnectorDto[];
  workflows: WorkflowSummary[];
  tickets: WorkItemDto[];
}): Etape[] {
  const reglages = { href: `/p/${slug}/settings`, label: "settings" };
  const etapes: Etape[] = [];

  etapes.push(
    projet.status === "active"
      ? { id: "provisioning", titre: "project provisioned", etat: "fait", detail: "its repository, tracker and pipelines are in place." }
      : {
          id: "provisioning",
          titre: "project provisioned",
          etat: "a_faire",
          detail:
            projet.status === "provisioning"
              ? "provisioning is running — its steps are listed below."
              : `the project is ${projet.status}: nothing runs until it is active.`,
        },
  );

  const parDefaut = workflows.find((w) => w.is_default);
  etapes.push(
    workflows.length > 0
      ? {
          id: "workflow",
          titre: "a workflow published",
          etat: "fait",
          detail: `${workflows.length} workflow${workflows.length > 1 ? "s" : ""}${parDefaut ? `, ${parDefaut.name} by default` : ""}.`,
        }
      : {
          id: "workflow",
          titre: "a workflow published",
          etat: "a_faire",
          detail: "a request has nowhere to go until a workflow says who does what.",
          lien: { href: `/p/${slug}/workflows/new`, label: "write one" },
        },
  );

  const exigees = new Set(exigences.map((e) => e.capability));
  for (const exigence of exigences) {
    const connecteur = exigence.connector ?? connecteurs.find((c) => c.kind === exigence.capability);
    const titre = `${libelleDeSorte(exigence.capability)} connected`;
    const pourquoi = exigence.reasons[0] ?? "a workflow needs it";
    const id = `connecteur-${exigence.capability}`;
    if (connecteur) {
      etapes.push(etapeDeConnecteur(id, titre, connecteur, pourquoi, reglages));
    } else if (exigence.default_type) {
      etapes.push({ id, titre, etat: "fait", detail: `platform default: ${exigence.default_type}.` });
    } else {
      etapes.push({ id, titre, etat: "a_faire", detail: `not connected — ${pourquoi}.`, lien: reglages });
    }
  }
  // Un connecteur qu'aucun workflow n'exige mais qui échoue se dit aussi : quelqu'un l'a mis là.
  for (const connecteur of connecteurs) {
    if (exigees.has(connecteur.kind) || connecteur.status === "ok" || connecteur.status === "unknown") continue;
    etapes.push(
      etapeDeConnecteur(
        `connecteur-${connecteur.kind}`,
        `${libelleDeSorte(connecteur.kind)} connected`,
        connecteur,
        "configured on this project",
        reglages,
      ),
    );
  }

  etapes.push(
    tickets.length > 0
      ? { id: "premier-ticket", titre: "a first request", etat: "fait", detail: "the project has tickets." }
      : {
          id: "premier-ticket",
          titre: "a first request",
          etat: "a_faire",
          detail: "file one to see the workflow run end to end.",
          lien: { href: `/p/${slug}/board`, label: "board" },
        },
  );
  return etapes;
}

function etapeDeConnecteur(
  id: string,
  titre: string,
  connecteur: ConnectorDto,
  pourquoi: string,
  reglages: { href: string; label: string },
): Etape {
  if (connecteur.status === "ok") return { id, titre, etat: "fait", detail: `${connecteur.type}, tested.` };
  if (connecteur.status === "unknown") {
    return { id, titre, etat: "a_verifier", detail: `${connecteur.type}, never tested — ${pourquoi}.`, lien: reglages };
  }
  return {
    id,
    titre,
    etat: "a_faire",
    detail: `${connecteur.type}: its last test ${connecteur.status === "degraded" ? "was degraded" : "failed"}${
      connecteur.last_error ? ` (${connecteur.last_error})` : ""
    }.`,
    lien: reglages,
  };
}

/** La liste de mise en route, en tête de la vue d'ensemble — absente quand tout est fait. */
export function MiseEnRoute({ slug, projet }: { slug: string; projet: ProjectDto | undefined }) {
  const exigences = useQuery({ queryKey: ["requirements", slug], queryFn: () => api.projectRequirements(slug) });
  const connecteurs = useQuery({ queryKey: ["connectors", slug], queryFn: () => api.connectors(slug) });
  const workflows = useQuery({ queryKey: ["workflows", slug], queryFn: () => api.workflows(slug) });
  const tickets = useQuery({ queryKey: ["items", slug], queryFn: () => api.workItems(slug) });
  if (!projet || !exigences.data || !connecteurs.data || !workflows.data || !tickets.data) return null;
  const etapes = etapesDeMiseEnRoute({
    slug,
    projet,
    exigences: exigences.data,
    connecteurs: connecteurs.data,
    workflows: workflows.data,
    tickets: tickets.data.items,
  });
  const faites = etapes.filter((e) => e.etat === "fait").length;
  if (faites === etapes.length) return null;
  return (
    <Card
      title="getting started"
      action={
        <span className="text-xs text-ink-muted">
          {faites} of {etapes.length} done
        </span>
      }
    >
      <ol className="divide-y divide-line" data-testid="mise-en-route">
        {etapes.map((etape) => (
          <li key={etape.id} className="flex items-start gap-3 py-2.5 text-sm" data-testid={`etape-${etape.id}`}>
            <span className="mt-0.5 inline-flex size-4 shrink-0 items-center justify-center">
              {etape.etat === "a_verifier" ? <Dot tone="warn" size={8} /> : <CheckSquare checked={etape.etat === "fait"} />}
            </span>
            <span className="min-w-0 flex-1">
              <span className={etape.etat === "fait" ? "text-ink-muted" : "font-medium"}>{etape.titre}</span>
              <span className="sr-only">{` (${etape.etat === "fait" ? "done" : etape.etat === "a_verifier" ? "to check" : "to do"})`}</span>
              <span className="block text-xs text-ink-muted">{etape.detail}</span>
            </span>
            {etape.lien && etape.etat !== "fait" && (
              <Link href={etape.lien.href} className="shrink-0 text-xs">
                {etape.lien.label}
              </Link>
            )}
          </li>
        ))}
      </ol>
    </Card>
  );
}
