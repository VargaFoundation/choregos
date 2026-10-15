// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { Statut, propose } from "@/components/actions/statut";
import { DecisionBar } from "@/components/decision-bar";
import { Badge, Button, Card, Empty, ErrorNote, Eyebrow, Heading } from "@/components/ui";
import { libelleDeDemande, phraseDeBudget, resumeDeDemande, useADecider } from "@/lib/a-decider";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import { useSession } from "@/lib/session";

/**
 * La boîte de ce qui attend une personne (S23-02) : les tickets arrêtés sur une demande humaine,
 * décidés ici comme sur le board, puis les actions gouvernées qui attendent leur approbation
 * (ADR 0035). Les groupes ne sont pas encore vérifiés : chacun voit tout ce qui attend dans
 * l'organisation, et la page le dit plutôt que de laisser croire à un filtre.
 */
export function InboxView({ project, heading = "h1" }: { project?: string; heading?: "h1" | "h2" }) {
  const { org } = useSession();
  const queryClient = useQueryClient();
  const boite = useADecider(org, project);
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Eyebrow>What waits for a person</Eyebrow>
        <Heading as={heading} size="xl">
          Inbox
        </Heading>
        <p className="max-w-3xl text-sm text-ink-muted">
          {project ? (
            <>
              Everything in project {project} that cannot move until someone decides: a ticket that asks for an
              approval, an answer or a task, and a write into a system outside Choregos. Groups are not checked yet —
              you see what is addressed to others too.{" "}
              <Link href="/inbox">The whole organisation&apos;s inbox</Link>
            </>
          ) : (
            <>
              Everything in organisation {org} that cannot move until someone decides: a ticket that asks for an
              approval, an answer or a task, and a write into a system outside Choregos. Groups are not checked yet —
              you see what is addressed to others too.
            </>
          )}
        </p>
      </div>
      {boite.erreur && <ErrorNote>{String(boite.erreur)}</ErrorNote>}
      {boite.projetsIllisibles.length > 0 && (
        <ErrorNote>
          The tickets of {boite.projetsIllisibles.join(", ")} could not be read: what waits there is not listed.
        </ErrorNote>
      )}

      <Card title="Tickets" action={<span className="text-xs text-ink-muted">{boite.tickets.length} waiting</span>}>
        {!boite.pret ? (
          <p className="text-sm text-ink-muted">Reading…</p>
        ) : boite.tickets.length === 0 ? (
          <Empty title="No ticket waits">Every ticket in flight is in the hands of an agent or the platform.</Empty>
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
                  {!project && (
                    <>
                      <span data-donnee>{item.project_slug}</span> ·{" "}
                    </>
                  )}
                  {item.tracker_key} · {item.state_display ?? item.state} · asked{" "}
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
                    Do the task
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

      <Card title="Actions to approve" action={<span className="text-xs text-ink-muted">{boite.actions.length} waiting</span>}>
        <p className="mb-3 max-w-3xl text-sm text-ink-muted">
          Writes into systems that are not ours — an account, a laptop order, a badge — run only once a person approves
          them, with a recent sign-in. The platform runs them afterwards, effect by effect, and undoes what it did if a
          later effect is refused.
        </p>
        {!boite.pret ? (
          <p className="text-sm text-ink-muted">Reading…</p>
        ) : boite.actions.length === 0 ? (
          <Empty title="Nothing waits">No action needs a decision.</Empty>
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
                  {!project && (
                    <>
                      <span data-donnee>{action.project_slug}</span> ·{" "}
                    </>
                  )}
                  proposed by {propose(action.proposed_by)}
                  {action.created_at ? ` · ${relative(action.created_at)}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Budgets crossed" action={<span className="text-xs text-ink-muted">{boite.budgets.length} open</span>}>
        <p className="mb-3 max-w-3xl text-sm text-ink-muted">
          A project&apos;s or an agent&apos;s daily budget that reached the policy&apos;s alert share (80 % by default)
          or the whole budget. One item per budget, threshold and day.
        </p>
        {!boite.pret ? (
          <p className="text-sm text-ink-muted">Reading…</p>
        ) : boite.budgets.length === 0 ? (
          <Empty title="No budget crossed">Nothing has reached its alert threshold today.</Empty>
        ) : (
          <ul className="space-y-2" data-testid="budgets-franchis">
            {boite.budgets.map((alerte) => (
              <li key={alerte.id} className="flex flex-wrap items-center gap-3 border-t border-line pt-2 text-sm">
                <Badge tone={alerte.threshold >= 100 ? "danger" : "warn"}>{alerte.threshold} %</Badge>
                <span>{phraseDeBudget(alerte)}</span>
                <Link href={alerte.scope === "agent" ? `/agents/${alerte.subject}` : `/costs?project=${alerte.subject}`} className="text-xs">
                  {alerte.scope === "agent" ? "The agent" : "Its spend"}
                </Link>
                <Button
                  size="sm"
                  onClick={async () => {
                    await api.acknowledgeBudgetAlert(org, alerte.id);
                    await queryClient.invalidateQueries({ queryKey: ["budget-alerts", org] });
                  }}
                >
                  Acknowledge
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
