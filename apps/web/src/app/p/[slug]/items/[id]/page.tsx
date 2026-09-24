"use client";

import { Heading } from "@varga/design-system";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";
import { DecisionBar } from "@/components/decision-bar";
import { ActorIcon, Button, Card, CostChip, Empty, ErrorNote, StateBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { eur, relative, shortDate, tokens, usd } from "@/lib/format";

export default function WorkItemPage({ params }: { params: Promise<{ slug: string; id: string }> }) {
  const { slug, id } = use(params);
  const queryClient = useQueryClient();
  const item = useQuery({ queryKey: ["item", id], queryFn: () => api.workItem(id) });
  const timeline = useQuery({ queryKey: ["timeline", id], queryFn: () => api.timeline(id) });
  const runs = useQuery({ queryKey: ["runs", id], queryFn: () => api.runs(id) });

  if (item.error) return <ErrorNote>{(item.error as Error).message}</ErrorNote>;
  if (!item.data) return <Empty>chargement…</Empty>;
  const data = item.data;

  async function control(action: string) {
    await api.action(id, action);
    queryClient.invalidateQueries({ queryKey: ["item", id] });
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Heading as="h2" size="lg">
            {data.title}
          </Heading>
          <p className="font-mono text-xs text-ink-muted">
            {data.tracker_key} · workflow {data.workflow_name} v{data.workflow_version}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StateBadge state={data.state} display={data.state_display} />
          <CostChip costEur={data.totals?.cost_eur} tokensIn={data.totals?.tokens_in} />
          <Button onClick={() => control(data.paused ? "resume" : "pause")}>
            {data.paused ? "Reprendre" : "Mettre en pause"}
          </Button>
          <Button tone="danger" onClick={() => control("stop")}>
            arrêter
          </Button>
        </div>
      </div>

      {(data.failure || (data.workflow_status && ["FAILED", "TERMINATED", "TIMED_OUT"].includes(data.workflow_status))) && (
        <ErrorNote>
          <strong>Ce ticket est mort.</strong> Son interpréteur s&apos;est arrêté
          {data.workflow_status ? ` (${data.workflow_status})` : ""}
          {data.failure?.activity ? ` dans ${data.failure.activity}` : ""} : il ne bougera plus tant qu&apos;on ne le
          relance pas.
          {data.failure?.message && <span className="mt-2 block font-mono text-xs">{data.failure.message}</span>}
          {data.failure?.at && <span className="mt-1 block text-xs">{relative(data.failure.at)}</span>}
        </ErrorNote>
      )}

      {data.pending_request && (
        <Card title="décision attendue">
          <p className="mb-2 text-sm">
            {String(data.pending_request.payload?.question ?? data.pending_request.payload?.summary ?? "")}
          </p>
          <DecisionBar
            itemId={id}
            kind={data.pending_request.kind}
            onDone={() => queryClient.invalidateQueries({ queryKey: ["item", id] })}
          />
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="coût par étape" className="lg:col-span-2">
          <table>
            <thead>
              <tr>
                <th>étape</th>
                <th>backend · modèle</th>
                <th className="text-right">tokens</th>
                <th className="text-right">coût</th>
                <th>résultat</th>
              </tr>
            </thead>
            <tbody>
              {(runs.data ?? []).map((run) => (
                <tr key={run.id}>
                  <td>
                    <Link href={`/p/${slug}/runs/${run.id}`} className="no-underline">
                      {run.stage_role} · {run.attempt}
                    </Link>
                  </td>
                  <td className="text-xs text-ink-muted">
                    {run.backend} · {(run.model ?? "").split("/").pop()}
                  </td>
                  <td className="text-right font-mono text-xs">
                    {tokens(run.tokens?.tokens_in)} / {tokens(run.tokens?.tokens_out)}
                  </td>
                  <td className="text-right">{usd(run.cost_usd)}</td>
                  <td className="max-w-80 truncate text-xs text-ink-muted">{run.result?.summary ?? run.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {runs.data?.length === 0 && <Empty>aucun run pour l&apos;instant</Empty>}
          <p className="mt-3 text-sm">
            Total {eur(data.totals?.cost_eur)}
            {data.estimate?.median_usd ? (
              <span className="text-ink-muted">
                {" "}
                · estimé {usd(data.estimate.median_usd)} (p80 {usd(data.estimate.p80_usd)})
                {data.estimate.over_p80 ? " ⚠ dépassement" : ""}
              </span>
            ) : null}
          </p>
        </Card>

        <Card title="timeline">
          <ol className="space-y-3">
            {(timeline.data ?? []).map((entry, index) => (
              <li key={`${entry.ts}-${index}`} className="border-l-2 border-line pl-3">
                <p className="flex items-center gap-2 text-sm">
                  <ActorIcon kind={entry.actor_kind ?? "system"} name={entry.actor ?? undefined} />
                  <span>{entry.title}</span>
                </p>
                {entry.detail && <p className="text-xs text-ink-muted">{entry.detail}</p>}
                <p className="text-xs text-ink-muted" title={shortDate(entry.ts)}>
                  {relative(entry.ts)}
                  {entry.cost_usd ? ` · ${usd(entry.cost_usd)}` : ""}
                </p>
              </li>
            ))}
          </ol>
          {timeline.data?.length === 0 && <Empty>rien à afficher</Empty>}
        </Card>
      </div>

      {data.pr_url && (
        <Card title="pull request">
          <a href={data.pr_url} target="_blank" rel="noreferrer" className="text-sm">
            {data.pr_url}
          </a>
        </Card>
      )}
    </div>
  );
}
