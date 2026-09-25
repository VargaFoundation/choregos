"use client";

import { Card } from "@varga/design-system";
import { Empty } from "@/components/ui";

export type AccesRun = {
  evenements: number;
  refus: number;
  /** décisions dont la nature a été déduite du titre : l'agent n'avait pas dit `kind` */
  deduites?: number;
  cout_outils_eur?: number;
  acces: { nature: string; cible: string; demandes: number; refus: number; motifs?: string[] }[];
};

const LIBELLE: Record<string, string> = {
  read: "read",
  write: "write",
  execute: "execute",
  network: "network",
  tool: "tool",
  autre: "other",
};

/**
 * Ce à quoi l'agent a touché.
 *
 * Tout était déjà journalisé — chaque permission, chaque refus et son motif — mais sous
 * forme de journal ACP brut : deux agents produisent deux cents événements en quelques
 * minutes, et personne ne les lit. Un journal que personne ne lit n'est pas un audit.
 *
 * Les refus passent devant, avec leur motif : c'est ce qu'on vient chercher.
 */
export function Acces({ acces }: { acces?: AccesRun }) {
  const lignes = acces?.acces ?? [];
  const refuses = lignes.filter((l) => l.refus > 0);

  return (
    <Card
      title="access"
      action={
        <span className="text-xs text-ink-muted">
          {acces
            ? `${acces.evenements} events · ${acces.refus} refusals${acces.deduites ? ` · ${acces.deduites} kinds inferred` : ""}`
            : "—"}
        </span>
      }
    >
      {lignes.length === 0 ? (
        <Empty>no access recorded for this run.</Empty>
      ) : (
        <>
          {refuses.length > 0 && (
            <div className="mb-4 border-l-2 border-danger pl-3">
              <p className="mb-1 text-xs uppercase tracking-wide text-danger">refused</p>
              <ul className="space-y-1 text-sm">
                {refuses.map((ligne) => (
                  <li key={`${ligne.nature}:${ligne.cible}`}>
                    <span className="font-mono text-xs">{ligne.cible}</span>
                    <span className="text-ink-muted"> — {ligne.motifs?.join(" · ") || "no reason"}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <table>
            <thead>
              <tr>
                <th>kind</th>
                <th>target</th>
                <th>requests</th>
              </tr>
            </thead>
            <tbody>
              {lignes.map((ligne) => (
                <tr key={`${ligne.nature}:${ligne.cible}`}>
                  <td className="text-xs">{LIBELLE[ligne.nature] ?? ligne.nature}</td>
                  <td>
                    <span className="font-mono text-xs break-all">{ligne.cible}</span>
                  </td>
                  <td className="text-xs">
                    {ligne.demandes}
                    {ligne.refus > 0 && <span className="text-danger"> · {ligne.refus} refused</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {acces?.cout_outils_eur ? (
            <p className="mt-2 text-xs text-ink-muted">
              catalogue tools: {acces.cout_outils_eur.toFixed(4)} €
            </p>
          ) : null}
        </>
      )}
    </Card>
  );
}
