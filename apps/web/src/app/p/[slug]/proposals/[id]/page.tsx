// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Code } from "@varga/design-system";
import { use, useState } from "react";
import { Button, Card, ErrorNote, StateBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import type { Proposal } from "@/lib/types";

/**
 * Une proposition d'action, et la décision humaine qui la tranche (ADR 0030).
 *
 * La décision exige une authentification RÉCENTE quand la politique le dit (`step_up_minutes`) :
 * l'API répond alors 401 `step_up_required`, et le client repasse par l'IdP avec `reauth=1` avant de
 * revenir ici. Qui a proposé ne décide pas (séparation des rôles). Un rejet exige un motif.
 */
export default function ProposalPage({ params }: { params: Promise<{ slug: string; id: string }> }) {
  const { slug, id } = use(params);
  const queryClient = useQueryClient();
  const proposition = useQuery({ queryKey: ["proposal", slug, id], queryFn: () => api.proposal(slug, id) });
  const [motif, setMotif] = useState("");
  const [erreur, setErreur] = useState<string | null>(null);
  const p = proposition.data;

  async function decider(decision: "approve" | "reject") {
    setErreur(null);
    try {
      await api.decideProposal(slug, id, { decision, reason: motif.trim() || undefined });
      setMotif("");
      void queryClient.invalidateQueries({ queryKey: ["proposal", slug, id] });
      void queryClient.invalidateQueries({ queryKey: ["proposals", slug] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "decision refused");
    }
  }

  if (!p) return <p className="text-sm text-ink-muted">loading the proposal…</p>;
  return (
    <div className="space-y-4">
      {erreur && <ErrorNote>{erreur}</ErrorNote>}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title={p.action_type} className="lg:col-span-2">
          <p className="text-sm">
            <StateBadge state={p.status} display={p.status.replace("_", " ")} kind="wait" /> proposed by{" "}
            <strong>{p.proposed_by.id}</strong>
            {p.proposed_by.via ? ` via ${p.proposed_by.via}` : ""}
            {p.created_at ? `, ${shortDate(p.created_at)}` : ""}
          </p>
          <p className="mt-3 text-sm">{p.justification}</p>
          <dl className="mt-3 grid grid-cols-[8rem_1fr] gap-x-4 gap-y-1 text-xs">
            <dt className="text-ink-muted">target</dt>
            <dd>{p.target.length ? p.target.map((t) => <Code key={t}>{t}</Code>) : "—"}</dd>
            <dt className="text-ink-muted">parameters</dt>
            <dd>
              <pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify(p.params, null, 2)}</pre>
            </dd>
          </dl>
        </Card>
        <Decision p={p} motif={motif} setMotif={setMotif} decider={decider} />
      </div>
      <Card title="history">
        <ul className="space-y-1 text-xs">
          {p.decisions.map((d, index) => (
            <li key={index}>
              {shortDate(d.at)} — <strong>{d.decision}</strong> by {d.by}
              {d.reason ? ` : ${d.reason}` : ""}
            </li>
          ))}
          {p.effects.map((effet, index) => (
            <li key={`e${index}`} className="font-mono">
              effect {String(effet.type ?? index)} : {String(effet.status ?? "")} {effet.url ? String(effet.url) : ""}
            </li>
          ))}
          {p.evidence.map((preuve, index) => (
            <li key={`p${index}`} className="font-mono">
              evidence {String(preuve.name ?? index)} : {String(preuve.status ?? "")}
            </li>
          ))}
        </ul>
        {p.decisions.length + p.effects.length + p.evidence.length === 0 && (
          <p className="text-xs text-ink-muted">nothing decided yet</p>
        )}
      </Card>
    </div>
  );
}

function Decision({
  p,
  motif,
  setMotif,
  decider,
}: {
  p: Proposal;
  motif: string;
  setMotif: (motif: string) => void;
  decider: (decision: "approve" | "reject") => Promise<void>;
}) {
  if (p.status !== "pending_approval") {
    return (
      <Card title="decision">
        <p className="text-sm text-ink-muted">this proposal is {p.status.replace("_", " ")}: nothing to decide.</p>
      </Card>
    );
  }
  const roles = (p.approval.approvers ?? []).map((a) => a.role).join(", ") || "owner";
  return (
    <Card title="decision">
      <div className="space-y-3 text-sm">
        <p className="text-xs text-ink-muted">
          decided by a project {roles}. The person who proposed it cannot decide.
        </p>
        {p.approval.step_up_minutes != null && (
          <Alert tone="neutral" title="you may be asked to sign in again">
            approving needs a sign-in less than {p.approval.step_up_minutes} minutes old. Choregos sends you to
            your identity provider, then back here: decide again.
          </Alert>
        )}
        <label className="block">
          <span className="text-xs text-ink-muted">reason (required to reject)</span>
          <textarea
            aria-label="reason"
            value={motif}
            onChange={(e) => setMotif(e.target.value)}
            rows={3}
            className="mt-1 w-full rounded border border-line bg-surface p-2 text-sm"
          />
        </label>
        <div className="flex gap-2">
          <Button tone="primary" onClick={() => void decider("approve")}>
            approve
          </Button>
          <Button tone="danger" disabled={!motif.trim()} onClick={() => void decider("reject")}>
            reject
          </Button>
        </div>
        {!motif.trim() && <p className="text-xs text-ink-muted">a reason is required to reject</p>}
      </div>
    </Card>
  );
}
