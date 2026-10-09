// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge } from "@varga/design-system";
import { Button, Card, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import type { ProjectOperation } from "@/lib/types";

const POLITIQUES = ["allowed", "approval", "forbidden"];
const TON = { allowed: "ok", approval: "warn", forbidden: "danger" } as const;

/** Ce qu'un projet peut choisir : la politique de l'organisation, ou plus strict (ADR 0034). */
export function choixPermis(politiqueDeLOrganisation: string): string[] {
  return POLITIQUES.slice(Math.max(0, POLITIQUES.indexOf(politiqueDeLOrganisation)));
}

/**
 * Les opérations que l'organisation ouvre à ce projet, et ce qu'il en resserre. Rien ne s'y
 * élargit : le choix ne propose que la politique de l'organisation ou plus strict.
 */
export function OperationsDuProjet({ slug }: { slug: string }) {
  const operations = useQuery({ queryKey: ["project-operations", slug], queryFn: () => api.projectOperations(slug) });
  if (operations.error) return <ErrorNote>{String(operations.error)}</ErrorNote>;
  if (!operations.data || operations.data.length === 0) return null;
  return (
    <Card title="operations of the organisation's connectors" className="lg:col-span-2">
      <div className="overflow-x-auto">
        <table className="w-full text-sm" data-testid="operations-du-projet">
          <thead className="text-left text-xs text-ink-muted">
            <tr>
              <th className="py-1 font-normal">connector</th>
              <th className="py-1 font-normal">operation</th>
              <th className="py-1 font-normal">organisation</th>
              <th className="py-1 font-normal">this project</th>
              <th className="py-1 font-normal">effective</th>
            </tr>
          </thead>
          <tbody>
            {operations.data.map((o) => (
              <Ligne key={`${o.connector}/${o.operation}`} slug={slug} operation={o} />
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Ligne({ slug, operation }: { slug: string; operation: ProjectOperation }) {
  const client = useQueryClient();
  const [erreur, setErreur] = useState<string | null>(null);
  async function agir(geste: () => Promise<unknown>) {
    setErreur(null);
    try {
      await geste();
      await client.invalidateQueries({ queryKey: ["project-operations", slug] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
    }
  }
  const id = `resserrer-${operation.connector}-${operation.operation}`;
  return (
    <tr className="border-t border-line">
      <td className="py-2 text-xs">{operation.connector}</td>
      <td>
        <code>{operation.operation}</code> <span className="text-xs text-ink-muted">{operation.access}</span>
        {erreur && <ErrorNote>{erreur}</ErrorNote>}
      </td>
      <td>
        <Badge tone={TON[operation.org_policy as keyof typeof TON] ?? "neutral"}>{operation.org_policy}</Badge>
      </td>
      <td className="whitespace-nowrap">
        <label className="sr-only" htmlFor={id}>
          this project&apos;s policy for {operation.operation}
        </label>
        <select
          id={id}
          className="rounded border border-line bg-surface px-2 py-1 text-xs"
          value={operation.project_policy ?? ""}
          onChange={(event) =>
            void agir(() =>
              event.target.value
                ? api.tightenProjectOperation(slug, operation.connector, operation.operation, event.target.value)
                : api.relaxProjectOperation(slug, operation.connector, operation.operation),
            )
          }
        >
          <option value="">as the organisation</option>
          {choixPermis(operation.org_policy).map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        {operation.project_policy && (
          <span className="ml-2">
            <Button
              size="sm"
              onClick={() => void agir(() => api.relaxProjectOperation(slug, operation.connector, operation.operation))}
            >
              reset
            </Button>
          </span>
        )}
      </td>
      <td>
        <Badge tone={TON[operation.effective_policy as keyof typeof TON] ?? "neutral"}>
          {operation.effective_policy}
        </Badge>
      </td>
    </tr>
  );
}
