// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { Badge, Eyebrow, Heading } from "@varga/design-system";
import { Statut, propose } from "@/components/actions/statut";
import { DecisionBar } from "@/components/decision-bar";
import { Card, Empty, ErrorNote } from "@/components/ui";
import { libelleDeDemande, resumeDeDemande, useADecider } from "@/lib/a-decider";
import { relative } from "@/lib/format";
import { useSession } from "@/lib/session";

/**
 * La boîte de ce qui attend une personne (S23-02) : les tickets arrêtés sur une demande humaine,
 * décidés ici comme sur le board, puis les actions gouvernées qui attendent leur approbation
 * (ADR 0035). Les groupes ne sont pas encore vérifiés : chacun voit tout ce qui attend dans
 * l'organisation, et la page le dit plutôt que de laisser croire à un filtre.
 */
export default function InboxPage() {
  const { org } = useSession();
  const queryClient = useQueryClient();
  const boite = useADecider(org);
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Eyebrow>what waits for a person</Eyebrow>
        <Heading as="h1" size="xl">
          inbox
        </Heading>
        <p className="max-w-3xl text-sm text-ink-muted">
          Everything in organisation {org} that cannot move until someone decides: a ticket that asks for an
          approval, an answer or a task, and a write into a system outside Choregos. Groups are not checked yet — you
          see what is addressed to others too.
        </p>
      </div>
      {boite.erreur && <ErrorNote>{String(boite.erreur)}</ErrorNote>}
      {boite.projetsIllisibles.length > 0 && (
        <ErrorNote>
          the tickets of {boite.projetsIllisibles.join(", ")} could not be read: what waits there is not listed.
        </ErrorNote>
      )}

      <Card title="tickets" action={<span className="text-xs text-ink-muted">{boite.tickets.length} waiting</span>}>
        {!boite.pret ? (
          <p className="text-sm text-ink-muted">reading…</p>
        ) : boite.tickets.length === 0 ? (
          <Empty title="no ticket waits">Every ticket in flight is in the hands of an agent or the platform.</Empty>
        ) : (
          <ul className="divide-y divide-line" data-testid="tickets-en-attente">
            {boite.tickets.map(({ item, demande, enRetard }) => (
              <li key={item.id} className="space-y-2 py-4 first:pt-0" data-testid={`attente-${item.id}`}>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <Badge tone={demande.kind === "approval" ? "ink" : "neutral"}>{libelleDeDemande(demande.kind)}</Badge>
                  <Link href={`/p/${item.project_slug}/items/${item.id}`} className="font-medium hover:underline">
                    {item.title}
                  </Link>
                </div>
                <p className="text-sm">{resumeDeDemande(demande)}</p>
                <p className="text-xs text-ink-muted">
                  {item.project_slug} · {item.tracker_key} · {item.state_display ?? item.state} · asked{" "}
                  {relative(demande.requested_at)}
                  {demande.due_at && (
                    <span className={enRetard ? "font-medium text-danger" : undefined}>
                      {" "}
                      · {enRetard ? "overdue" : "due"} {relative(demande.due_at)}
                    </span>
                  )}
                </p>
                {demande.kind === "task" ? (
                  // Un formulaire et une attestation ne tiennent pas dans une ligne : la page du ticket.
                  <Link href={`/p/${item.project_slug}/items/${item.id}`} className="text-sm">
                    do the task
                  </Link>
                ) : (
                  <DecisionBar
                    itemId={item.id}
                    kind={demande.kind}
                    request={demande}
                    onDone={() => queryClient.invalidateQueries({ queryKey: ["items", item.project_slug] })}
                  />
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="actions to approve" action={<span className="text-xs text-ink-muted">{boite.actions.length} waiting</span>}>
        <p className="mb-3 max-w-3xl text-sm text-ink-muted">
          Writes into systems that are not ours — an account, a laptop order, a badge — run only once a person approves
          them, with a recent sign-in. The platform runs them afterwards, effect by effect, and undoes what it did if a
          later effect is refused.
        </p>
        {!boite.pret ? (
          <p className="text-sm text-ink-muted">reading…</p>
        ) : boite.actions.length === 0 ? (
          <Empty title="nothing waits">No action needs a decision.</Empty>
        ) : (
          <ul className="space-y-2" data-testid="boite">
            {boite.actions.map((action) => (
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
