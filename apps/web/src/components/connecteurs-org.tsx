// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Badge } from "@varga/design-system";
import { Card, ErrorNote } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { estUneSorteDuProjet, libelleDeSorte, SORTES_DU_PROJET } from "@/lib/sortes-de-connecteurs";
import type { ConnectorDto, OrgConnector, ProjectDto } from "@/lib/types";

/**
 * Les connecteurs de l'organisation se rangent en deux familles (revue du 07/10 : « ça mélange
 * des agents, des MCPs et des connecteurs API ») : les systèmes métier, qui DÉCLARENT leurs
 * opérations, et les serveurs MCP, dont on DÉCOUVRE les outils. L'agent d'un fournisseur servi
 * en MCP est, pour Choregos, un serveur MCP.
 */
export function rangerLesConnecteurs(instances: OrgConnector[]): { metier: OrgConnector[]; mcp: OrgConnector[] } {
  return {
    metier: instances.filter((instance) => instance.kind !== "mcp"),
    mcp: instances.filter((instance) => instance.kind === "mcp"),
  };
}

export type LigneDeLivraison = {
  slug: string;
  nom: string;
  outils: { kind: string; type: string; status: string }[] | null;
  refus: string | null;
};

/** Une ligne par projet : ses outils de livraison configurés, dans l'ordre des exigences, ou pourquoi on ne les voit pas. */
export function lignesDeLivraison(
  projets: Pick<ProjectDto, "slug" | "name">[],
  lectures: { data?: ConnectorDto[]; error?: unknown }[],
): LigneDeLivraison[] {
  return projets.map((projet, index) => {
    const lecture = lectures[index] ?? {};
    if (lecture.error) {
      const interdit = lecture.error instanceof ApiError && lecture.error.status === 403;
      return { slug: projet.slug, nom: projet.name, outils: null, refus: interdit ? "no access" : "could not read" };
    }
    if (!lecture.data) return { slug: projet.slug, nom: projet.name, outils: null, refus: null };
    const outils = lecture.data
      .filter((connecteur) => estUneSorteDuProjet(connecteur.kind))
      .sort((a, b) => SORTES_DU_PROJET.indexOf(a.kind as never) - SORTES_DU_PROJET.indexOf(b.kind as never))
      .map((connecteur) => ({ kind: connecteur.kind, type: connecteur.type, status: connecteur.status ?? "unknown" }));
    return { slug: projet.slug, nom: projet.name, outils, refus: null };
  });
}

/** Les outils de livraison de chaque projet, en lecture : ils se règlent dans les réglages du projet. */
export function OutilsDeLivraison({ org }: { org: string }) {
  const projets = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org) });
  const liste = projets.data?.items ?? [];
  const lectures = useQueries({
    queries: liste.map((projet) => ({
      queryKey: ["connectors", projet.slug],
      queryFn: () => api.connectors(projet.slug),
      retry: false,
    })),
  });
  const lignes = lignesDeLivraison(liste, lectures);
  return (
    <Card title="project delivery tools">
      <div className="space-y-3 text-sm" id="delivery">
        <p className="max-w-3xl text-ink-muted">
          Each project&apos;s tracker, repository, CI and deployment — set in the project&apos;s settings, never here. A
          kind a project does not configure falls back to the platform default.
        </p>
        {projets.error ? (
          <ErrorNote>{String(projets.error)}</ErrorNote>
        ) : !projets.data ? (
          <p className="text-ink-muted">reading the projects…</p>
        ) : lignes.length === 0 ? (
          <p className="text-ink-muted">no project yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full" data-testid="outils-de-livraison">
              <thead className="text-left text-xs text-ink-muted">
                <tr>
                  <th className="py-1 font-normal">project</th>
                  <th className="py-1 font-normal">configured tools</th>
                  <th className="py-1 font-normal">
                    <span className="sr-only">actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {lignes.map((ligne) => (
                  <tr key={ligne.slug} className="border-t border-line" data-testid={`livraison-${ligne.slug}`}>
                    <td className="py-2 font-medium">{ligne.nom}</td>
                    <td className="py-2">
                      {ligne.refus ? (
                        <span className="text-xs text-ink-muted">{ligne.refus}</span>
                      ) : !ligne.outils ? (
                        <span className="text-xs text-ink-muted">reading…</span>
                      ) : ligne.outils.length === 0 ? (
                        <span className="text-xs text-ink-muted">platform defaults only</span>
                      ) : (
                        <span className="flex flex-wrap gap-2">
                          {ligne.outils.map((outil) => (
                            <span key={outil.kind} title={`status: ${outil.status}`}>
                              <Badge tone={outil.status === "error" ? "danger" : "neutral"}>
                                {libelleDeSorte(outil.kind)}: {outil.type}
                              </Badge>
                            </span>
                          ))}
                        </span>
                      )}
                    </td>
                    <td className="py-2 text-right">
                      <Link href={`/p/${ligne.slug}/settings`} className="text-xs underline">
                        settings
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {projets.data?.meta?.has_more && (
          <p className="text-xs text-ink-muted">and more projects: see the projects page.</p>
        )}
      </div>
    </Card>
  );
}
