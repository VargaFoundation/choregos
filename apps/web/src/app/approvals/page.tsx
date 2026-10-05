// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Eyebrow, Heading } from "@varga/design-system";
import { Statut, propose } from "@/components/actions/statut";
import { Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import { useSession } from "@/lib/session";

/**
 * La boîte des décisions (ADR 0035) : les actions gouvernées des projets de l'organisation qui
 * attendent quelqu'un — une écriture dans un annuaire, la commande d'un poste, l'activation d'un
 * badge. Rien ne s'exécute avant qu'une personne ré-authentifiée les approuve.
 */
export default function ApprovalsPage() {
  const { org } = useSession();
  const attente = useQuery({ queryKey: ["org-actions", org], queryFn: () => api.orgActions(org, "pending_approval") });
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Eyebrow>what waits for a person</Eyebrow>
        <Heading as="h1" size="xl">
          approvals
        </Heading>
        <p className="max-w-3xl text-sm text-ink-muted">
          Writes into systems that are not ours — an account, a laptop order, a badge — run only once a person
          approves them, with a recent sign-in. The platform runs them afterwards, effect by effect, and undoes what
          it did if a later effect is refused.
        </p>
      </div>
      <Card title="waiting">
        {attente.error ? (
          <ErrorNote>{String(attente.error)}</ErrorNote>
        ) : !attente.data ? (
          <p className="text-sm text-ink-muted">reading…</p>
        ) : attente.data.length === 0 ? (
          <Empty title="nothing waits">no action needs a decision.</Empty>
        ) : (
          <ul className="space-y-2" data-testid="boite">
            {attente.data.map((action) => (
              <li key={action.id} className="flex flex-wrap items-center gap-3 border-t border-line pt-2 text-sm">
                <Link
                  href={`/p/${action.project_slug ?? ""}/actions/${action.id}`}
                  className="font-medium hover:underline"
                >
                  {action.title}
                </Link>
                <Statut statut={action.status} />
                <span className="text-xs text-ink-muted">
                  {action.project_slug} · proposed by {propose(action.proposed_by)}
                  {action.created_at ? ` · ${relative(action.created_at)}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
