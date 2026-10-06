// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";
import { Heading } from "@varga/design-system";
import { DecisionDAction } from "@/components/actions/decision";
import { Statut, propose } from "@/components/actions/statut";
import { Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { relative, shortDate } from "@/lib/format";

/** Une action gouvernée : ce qu'elle fera, qui en a décidé, et le journal de ses effets clé par clé. */
export default function ActionPage({ params }: { params: Promise<{ slug: string; id: string }> }) {
  const { slug, id } = use(params);
  const action = useQuery({ queryKey: ["action", slug, id], queryFn: () => api.projectAction(slug, id) });
  if (action.error) return <ErrorNote>{String(action.error)}</ErrorNote>;
  if (!action.data) return <Empty>reading the action…</Empty>;
  const a = action.data;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <Link href={`/p/${slug}/actions`} className="text-sm text-ink-muted hover:underline">
          actions
        </Link>
        <span aria-hidden className="text-ink-muted">
          /
        </span>
        <Heading as="h1" size="lg">
          {a.title}
        </Heading>
        <Statut statut={a.status} />
      </div>
      <p className="text-sm text-ink-muted">
        {a.kind} · proposed by {propose(a.proposed_by)}
        {a.created_at ? ` · ${relative(a.created_at)}` : ""}
        {a.justification ? ` — ${a.justification}` : ""}
      </p>
      <Card title="decision">
        {a.status === "pending_approval" ? (
          <DecisionDAction projet={slug} action={a} />
        ) : (a.decisions ?? []).length === 0 ? (
          <p className="text-sm text-ink-muted">no decision recorded.</p>
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
      <Card title="effects">
        <ol className="space-y-2 text-sm">
          {(a.effects ?? []).map((spec, position) => {
            const fait = (a.journal ?? []).find((e) => e.position === position);
            return (
              <li key={position} className="border-t border-line pt-2" data-testid={`effet-${position}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <code>{String(spec.effect)}</code>
                  {fait ? <Statut statut={fait.status} /> : <span className="text-xs text-ink-muted">not started</span>}
                  {fait && <span className="text-xs text-ink-muted">key {fait.key} · {fait.attempts} attempt(s)</span>}
                </div>
                <pre className="mt-1 whitespace-pre-wrap break-words text-xs text-ink-muted">
                  {JSON.stringify(spec.with ?? {}, null, 1)}
                </pre>
                {spec.compensate ? (
                  <p className="text-xs text-ink-muted">undone by {String((spec.compensate as { effect?: string }).effect)}</p>
                ) : null}
                {fait?.error && <p className="text-xs text-danger">{fait.error}</p>}
              </li>
            );
          })}
        </ol>
        {a.error && <ErrorNote>{a.error}</ErrorNote>}
      </Card>
    </div>
  );
}
