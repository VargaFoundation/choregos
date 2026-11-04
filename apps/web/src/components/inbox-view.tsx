// SPDX-License-Identifier: Apache-2.0
"use client";

import { CircleCheck } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Status, propose } from "@/components/actions/status";
import { orgConnectorHref } from "@/lib/connector-link";
import { DecisionBar } from "@/components/decision-bar";
import { Badge, Button, Card, Empty, ErrorNote, Eyebrow, Heading } from "@/components/ui";
import { requestLabel, phraseDeBudget, requestSummary, useADecider } from "@/lib/a-decider";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import { FAMILY_LABEL } from "@/lib/egress";
import { useSession } from "@/lib/session";

/**
 * The inbox of what waits for a person (S23-02): the tickets stopped on a human request,
 * decided here as on the board, then the governed actions awaiting their approval
 * (ADR 0035). Groups are not verified yet: everyone sees everything that waits in
 * the organisation, and the page says so rather than suggest a filter.
 */
export function InboxView({ project, heading = "h1" }: { project?: string; heading?: "h1" | "h2" }) {
  const { org } = useSession();
  const queryClient = useQueryClient();
  const inbox = useADecider(org, project);
  // A developer sees the alert but is not offered the gesture (`can_acknowledge`); should the role
  // change between the read and the click, the API's refusal is shown, not swallowed.
  const [refus, setRefus] = useState<string | null>(null);
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
      {inbox.erreur && <ErrorNote>{String(inbox.erreur)}</ErrorNote>}
      {inbox.projetsIllisibles.length > 0 && (
        <ErrorNote>
          The tickets of {inbox.projetsIllisibles.join(", ")} could not be read: what waits there is not listed.
        </ErrorNote>
      )}

      <Card title="Tickets" action={<span className="text-xs text-ink-muted">{inbox.tickets.length} waiting</span>}>
        {!inbox.pret ? (
          <p className="text-sm text-ink-muted">Reading…</p>
        ) : inbox.tickets.length === 0 ? (
          <Empty title="No ticket waits" icon={CircleCheck} hue={6}>Every ticket in flight is in the hands of an agent or the platform.</Empty>
        ) : (
          <ul className="divide-y divide-line" data-testid="waiting-tickets">
            {inbox.tickets.map(({ item, request, overdue }) => (
              <li key={item.id} className="space-y-2 py-4 first:pt-0" data-testid={`waiting-${item.id}`}>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <Badge tone={request.kind === "approval" ? "ink" : "neutral"}>{requestLabel(request.kind)}</Badge>
                  <Link href={`/p/${item.project_slug}/items/${item.id}`} className="font-medium hover:underline">
                    {item.title}
                  </Link>
                </div>
                <p className="text-sm">{requestSummary(request)}</p>
                <p className="text-xs text-ink-muted">
                  {!project && (
                    <>
                      <span data-verbatim>{item.project_slug}</span> ·{" "}
                    </>
                  )}
                  {item.tracker_key} · {item.state_display ?? item.state} · asked{" "}
                  {relative(request.requested_at)}
                  {request.due_at && (
                    <span className={overdue ? "font-medium text-danger" : undefined}>
                      {" "}
                      · {overdue ? "overdue" : "due"} {relative(request.due_at)}
                    </span>
                  )}
                </p>
                {request.kind === "task" ? (
                  // A form and an attestation do not fit in a row: the ticket's page.
                  <Link href={`/p/${item.project_slug}/items/${item.id}`} className="text-sm">
                    Do the task
                  </Link>
                ) : (
                  <DecisionBar
                    itemId={item.id}
                    kind={request.kind}
                    request={request}
                    onDone={() => queryClient.invalidateQueries({ queryKey: ["items", item.project_slug] })}
                  />
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Actions to approve" action={<span className="text-xs text-ink-muted">{inbox.actions.length} waiting</span>}>
        <p className="mb-3 max-w-3xl text-sm text-ink-muted">
          Writes into systems that are not ours — an account, a laptop order, a badge — run only once a person approves
          them, with a recent sign-in. The platform runs them afterwards, effect by effect, and undoes what it did if a
          later effect is refused.
        </p>
        {!inbox.pret ? (
          <p className="text-sm text-ink-muted">Reading…</p>
        ) : inbox.actions.length === 0 ? (
          <Empty title="Nothing waits" icon={CircleCheck} hue={6}>No action needs a decision.</Empty>
        ) : (
          <ul className="space-y-2" data-testid="inbox">
            {inbox.actions.map((action) => (
              <li key={action.id} className="flex flex-wrap items-center gap-3 border-t border-line pt-2 text-sm">
                <Link
                  href={`/p/${action.project_slug ?? ""}/actions/${action.id}`}
                  className="font-medium hover:underline"
                >
                  {action.title}
                </Link>
                <Status statut={action.status} />
                <span className="text-xs text-ink-muted">
                  {!project && (
                    <>
                      <span data-verbatim>{action.project_slug}</span> ·{" "}
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

      {!project && (
        <Card title="Network access" action={<span className="text-xs text-ink-muted">{inbox.egressRequests.length} waiting</span>}>
          <p className="mb-3 max-w-3xl text-sm text-ink-muted">
            A host somebody needs Choregos to call for the organisation — an MCP server, a git repository, a web site.
            An administrator allows it, or refuses it; the person asked retries once it is approved.
          </p>
          {!inbox.pret ? (
            <p className="text-sm text-ink-muted">Reading…</p>
          ) : inbox.egressRequests.length === 0 ? (
            <Empty title="No request waits" icon={CircleCheck} hue={6}>Nobody is waiting for a host to be opened.</Empty>
          ) : (
            <ul className="space-y-2" data-testid="egress-requests">
              {inbox.egressRequests.map((rule) => (
                <li key={rule.id} className="flex flex-wrap items-center gap-3 border-t border-line pt-2 text-sm" data-testid={`egress-${rule.id}`}>
                  <span className="font-medium" data-verbatim>
                    {rule.host}
                  </span>
                  <Badge tone="neutral">{rule.used_by.map((f) => FAMILY_LABEL[f]).join(", ")}</Badge>
                  <span className="text-xs text-ink-muted">
                    {rule.reason ? `${rule.reason} · ` : ""}
                    {rule.origin ? <OriginLink origin={rule.origin} /> : null} · asked by {rule.requested_by ?? "someone"}
                    {rule.requested_at ? ` ${relative(rule.requested_at)}` : ""}
                  </span>
                  {inbox.canDecideEgress ? (
                    <span className="flex gap-2">
                      <Button
                        size="sm"
                        tone="primary"
                        onClick={async () => {
                          await api.approveOrgEgressRequest(org, rule.id);
                          await queryClient.invalidateQueries({ queryKey: ["egress", org] });
                        }}
                      >
                        Approve
                      </Button>
                      <Button
                        size="sm"
                        onClick={async () => {
                          await api.refuseOrgEgressRequest(org, rule.id);
                          await queryClient.invalidateQueries({ queryKey: ["egress", org] });
                        }}
                      >
                        Refuse
                      </Button>
                    </span>
                  ) : (
                    <span className="text-xs text-ink-muted">an administrator decides</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      <Card title="Budgets crossed" action={<span className="text-xs text-ink-muted">{inbox.budgets.length} open</span>}>
        <p className="mb-3 max-w-3xl text-sm text-ink-muted">
          A project&apos;s or an agent&apos;s daily budget that reached the policy&apos;s alert share (80 % by default)
          or the whole budget. One item per budget, threshold and day. Acknowledging one takes it out of everyone&apos;s
          inbox, so it needs the project owner role or above.
        </p>
        {refus && <ErrorNote>{refus}</ErrorNote>}
        {!inbox.pret ? (
          <p className="text-sm text-ink-muted">Reading…</p>
        ) : inbox.budgets.length === 0 ? (
          <Empty title="No budget crossed">Nothing has reached its alert threshold today.</Empty>
        ) : (
          <ul className="space-y-2" data-testid="budgets-exceeded">
            {inbox.budgets.map((alerte) => (
              <li key={alerte.id} className="flex flex-wrap items-center gap-3 border-t border-line pt-2 text-sm">
                <Badge tone={alerte.threshold >= 100 ? "danger" : "warn"}>{alerte.threshold} %</Badge>
                <span>{phraseDeBudget(alerte)}</span>
                <Link href={alerte.scope === "agent" ? `/agents/${alerte.subject}` : `/costs?project=${alerte.subject}`} className="text-xs">
                  {alerte.scope === "agent" ? "The agent" : "Its spend"}
                </Link>
                {alerte.can_acknowledge ? (
                  <Button
                    size="sm"
                    onClick={async () => {
                      setRefus(null);
                      try {
                        await api.acknowledgeBudgetAlert(org, alerte.id);
                      } catch (erreur) {
                        setRefus(erreur instanceof Error ? erreur.message : String(erreur));
                        return;
                      }
                      await queryClient.invalidateQueries({ queryKey: ["budget-alerts", org] });
                    }}
                  >
                    Acknowledge
                  </Button>
                ) : (
                  <span className="text-xs text-ink-muted" data-testid={`budget-owner-${alerte.id}`}>
                    {alerte.scope === "agent"
                      ? "An organisation owner or administrator acknowledges it."
                      : "A project owner acknowledges it."}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

/** Where a request came from, so the inbox links back to the thing waiting for it. */
function OriginLink({ origin }: { origin: string }) {
  if (origin.startsWith("org-connector:")) return <Link href={orgConnectorHref(origin.slice(14))}>connector {origin.slice(14)}</Link>;
  if (origin.startsWith("skill-import:")) return <Link href="/skills">skills from {origin.slice(13)}</Link>;
  if (origin.startsWith("knowledge-source:")) return <Link href="/knowledge">source {origin.slice(17)}</Link>;
  return <span>{origin}</span>;
}
