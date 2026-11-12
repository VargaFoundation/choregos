// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";
import { DecisionDAction } from "@/components/actions/decision";
import { InDoubt } from "@/components/actions/in-doubt";
import { OntologyDetail } from "@/components/actions/ontology";
import { Status, propose } from "@/components/actions/status";
import { Card, Empty, ErrorNote, Heading } from "@/components/ui";
import { api } from "@/lib/api";
import { relative, shortDate } from "@/lib/format";

/** A governed action: what it will do, who decided on it, and the log of its effects key by key. */
export default function ActionPage({ params }: { params: Promise<{ slug: string; id: string }> }) {
  const { slug, id } = use(params);
  const action = useQuery({ queryKey: ["action", slug, id], queryFn: () => api.projectAction(slug, id) });
  if (action.error) return <ErrorNote>{String(action.error)}</ErrorNote>;
  if (!action.data) return <Empty>Reading the action…</Empty>;
  const a = action.data;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <Link href={`/p/${slug}/actions`} className="text-sm text-ink-muted hover:underline">
          Actions
        </Link>
        <span aria-hidden className="text-ink-muted">
          /
        </span>
        <Heading as="h2" size="lg" data-verbatim>
          {a.title}
        </Heading>
        <Status statut={a.status} />
      </div>
      <p className="text-sm text-ink-muted">
        <span data-verbatim>{a.kind}</span> · proposed by {propose(a.proposed_by)}
        {a.created_at ? ` · ${relative(a.created_at)}` : ""}
        {a.justification ? ` — ${a.justification}` : ""}
      </p>
      <InDoubt action={a} />
      <OntologyDetail action={a} />
      <Card title="Decision">
        {a.status === "pending_approval" ? (
          <DecisionDAction projet={slug} action={a} />
        ) : (a.decisions ?? []).length === 0 ? (
          <p className="text-sm text-ink-muted">No decision recorded.</p>
        ) : null}
        {(a.decisions ?? []).length > 0 && (
          <ul className="mt-2 space-y-1 text-sm" data-testid="decisions">
            {(a.decisions ?? []).map((d, index) => (
              <li key={index}>
                <strong>{String(d.decision)}</strong> by {String(d.by)}
                {d.at ? ` · ${shortDate(String(d.at))}` : ""}
                {d.reason ? ` — ${String(d.reason)}` : ""}
                {typeof d.auth_age_seconds === "number" ? (
                  <span className="text-xs text-ink-muted"> (signed in {Math.round(d.auth_age_seconds / 60)} min before)</span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Effects">
        <ol className="space-y-2 text-sm">
          {(a.effects ?? []).map((spec, position) => {
            const recorded = (a.journal ?? []).find((e) => e.position === position);
            return (
              <li key={position} className="border-t border-line pt-2" data-testid={`effect-${position}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <code>{String(spec.effect)}</code>
                  {recorded ? <Status statut={recorded.status} /> : <span className="text-xs text-ink-muted">Not started</span>}
                  {recorded && <span className="text-xs text-ink-muted">Key {recorded.key} · {recorded.attempts} attempt(s)</span>}
                </div>
                <pre className="mt-1 whitespace-pre-wrap break-words text-xs text-ink-muted">
                  {JSON.stringify(spec.with ?? {}, null, 1)}
                </pre>
                {spec.compensate ? (
                  <p className="text-xs text-ink-muted">Undone by {String((spec.compensate as { effect?: string }).effect)}</p>
                ) : null}
                {recorded?.error && <p className="text-xs text-danger">{recorded.error}</p>}
              </li>
            );
          })}
        </ol>
        {a.error && <ErrorNote>{a.error}</ErrorNote>}
      </Card>
    </div>
  );
}
