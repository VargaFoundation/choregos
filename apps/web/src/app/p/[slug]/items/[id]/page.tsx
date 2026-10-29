// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use, useState } from "react";
import { DecisionBar } from "@/components/decision-bar";
import { CarteDuParcours } from "@/components/parcours/carte-du-parcours";
import { gare } from "@/components/parcours/modele";
import { ActorIcon, Button, Card, CostChip, Dot, Empty, ErrorNote, Breadcrumbs, Heading, StateBadge, tonDActeur } from "@/components/ui";
import { api } from "@/lib/api";
import { libelleDeCode, relative, shortDate, tokens, usd } from "@/lib/format";
import type { WorkItemJourney } from "@/lib/types";
import { GesteConfirme } from "@/components/geste-confirme";
import { Artifacts } from "@/components/artifacts";
import { Passages } from "@/components/passages";
import { Provenance } from "@/components/provenance";
import { TrackerCard } from "@/components/who-writes";
import { viaInWords } from "@/lib/who-writes";

/** Tant qu'un agent tourne ou qu'une personne est attendue, le parcours se relit : la carte suit. */
function enMouvement(journey: WorkItemJourney | undefined): boolean {
  if (!journey || journey.closed) return false;
  return journey.steps.some(
    (s) => !s.ended_at && ["queued", "running", "waiting", "pending_approval", "approved"].includes(s.status),
  );
}

export default function WorkItemPage({ params }: { params: Promise<{ slug: string; id: string }> }) {
  const { slug, id } = use(params);
  const queryClient = useQueryClient();
  const [erreurDeControle, setErreurDeControle] = useState<string | null>(null);
  const item = useQuery({ queryKey: ["item", id], queryFn: () => api.workItem(id) });
  const timeline = useQuery({ queryKey: ["timeline", id], queryFn: () => api.timeline(id) });
  // Les reçus de passage (S25-04) : qui a produit chaque sortie d'étape, qui l'a lue, sous quelle empreinte.
  const passages = useQuery({ queryKey: ["hand-offs", id], queryFn: () => api.handOffs(id) });
  const runs = useQuery({ queryKey: ["runs", id], queryFn: () => api.runs(id) });
  // The tracker connector the bookkeeping writes through (ADR 0107 point 4).
  const connectors = useQuery({ queryKey: ["connectors", slug], queryFn: () => api.connectors(slug) });
  // What the steps left, typed, stored, digest-checked (ADR 0049).
  const artifacts = useQuery({ queryKey: ["artifacts", id], queryFn: () => api.artifacts(id) });
  const journey = useQuery({
    queryKey: ["journey", id],
    queryFn: () => api.journey(id),
    refetchInterval: (query) => (enMouvement(query.state.data) ? 4000 : false),
  });

  if (item.error) return <ErrorNote>{(item.error as Error).message}</ErrorNote>;
  if (!item.data) return <Empty>Loading…</Empty>;
  const data = item.data;

  async function control(action: string) {
    await api.action(id, action);
    await queryClient.invalidateQueries({ queryKey: ["item", id] });
  }

  return (
    <div className="space-y-4">
      <Breadcrumbs steps={[{ href: `/p/${slug}/board`, label: "Board" }, { label: data.tracker_key }]} />
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Heading as="h2" size="lg" data-donnee>
            {data.title}
          </Heading>
          <p className="font-mono text-xs text-ink-muted">
            <span data-donnee>{data.tracker_key}</span> · workflow {data.workflow_name} v{data.workflow_version}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StateBadge state={data.state} display={data.state_display} />
          <CostChip costUsd={data.totals?.cost_usd} tokensIn={data.totals?.tokens_in} />
          {gare(journey.data) && (
            <GesteConfirme
              ton="accent"
              question="Replay the stage that escalated? An agent runs it again from the start — a new run, billed."
              confirmer="Replay the stage"
              action={() => control("rerun_stage")}
            >
              Replay the stage
            </GesteConfirme>
          )}
          <Button
            onClick={() =>
              void control(data.paused ? "resume" : "pause").catch((cause: unknown) =>
                setErreurDeControle(cause instanceof Error ? cause.message : "refused"),
              )
            }
          >
            {data.paused ? "Resume" : "Pause"}
          </Button>
          <GesteConfirme
            tonDuBouton="danger"
            question="Stop this ticket? Its run is cancelled and it leaves the workflow — it does not resume, it has to be filed again."
            confirmer="Stop the ticket"
            action={() => control("stop")}
          >
            Stop
          </GesteConfirme>
        </div>
      </div>

      {erreurDeControle && <ErrorNote>{erreurDeControle}</ErrorNote>}
      {(data.failure ||
        (data.workflow_status && ["FAILED", "TERMINATED", "TIMED_OUT"].includes(data.workflow_status))) && (
        <ErrorNote>
          <strong>This ticket is dead.</strong> Its interpreter stopped
          {data.workflow_status ? ` (${libelleDeCode(data.workflow_status)})` : ""}
          {data.failure?.activity ? ` in ${data.failure.activity}` : ""}: it will not move again until it is restarted.
          {data.failure?.message && <span className="mt-2 block font-mono text-xs">{data.failure.message}</span>}
          {data.failure?.at && <span className="mt-1 block text-xs">{relative(data.failure.at)}</span>}
        </ErrorNote>
      )}

      {data.pending_request && (
        <Card title="Decision awaited">
          <p className="mb-2 text-sm">
            {String(data.pending_request.payload?.question ?? data.pending_request.payload?.summary ?? "")}
          </p>
          <DecisionBar
            itemId={id}
            kind={data.pending_request.kind}
            request={data.pending_request}
            onDone={() => queryClient.invalidateQueries({ queryKey: ["item", id] })}
          />
        </Card>
      )}

      <Card title="Journey">
        {journey.data ? (
          <CarteDuParcours
            journey={journey.data}
            slug={slug}
            itemId={id}
            onDecided={() => {
              void queryClient.invalidateQueries({ queryKey: ["journey", id] });
              void queryClient.invalidateQueries({ queryKey: ["item", id] });
            }}
          />
        ) : journey.error ? (
          <p className="text-sm text-ink-muted">The journey cannot be read: {(journey.error as Error).message}</p>
        ) : (
          <Empty>Loading the journey…</Empty>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Cost per stage" className="lg:col-span-2" action={<Provenance de="observed" />}>
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>Stage</th>
                  <th>Backend · model</th>
                  <th className="text-right">Tokens</th>
                  <th className="text-right">Cost</th>
                  <th>Result</th>
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
          </div>
          {runs.data?.length === 0 && <Empty>No run yet</Empty>}
          <p className="mt-3 text-sm">
            Total {usd(data.totals?.cost_usd)}
            {data.estimate?.median_usd ? (
              <span className="text-ink-muted">
                {" "}
                · estimated {usd(data.estimate.median_usd)} (p80 {usd(data.estimate.p80_usd)})
                {data.estimate.over_p80 ? " ⚠ over" : ""} <Provenance de="inferred" />
              </span>
            ) : null}
          </p>
        </Card>

        <Card title="Timeline">
          {/* Un rail, et sur lui un point par moment, à la couleur de qui a agi (S24-04). */}
          <ol className="relative space-y-3 before:absolute before:top-2 before:bottom-2 before:left-[3.5px] before:w-px before:bg-line">
            {(timeline.data ?? []).map((entry, index) => (
              <li key={`${entry.ts}-${index}`} className="relative pl-5">
                <Dot tone={tonDActeur(entry.actor_kind ?? "system")} size={8} className="absolute top-1.5 left-0" />
                <p className="flex items-center gap-2 text-sm">
                  <ActorIcon kind={entry.actor_kind ?? "system"} name={entry.actor ?? undefined} point={false} />
                  <span>{entry.title}</span>
                </p>
                {entry.detail && (
                  <p className="text-xs text-ink-muted" data-donnee>
                    {entry.detail}
                  </p>
                )}
                <p className="font-mono text-xs text-ink-muted" title={shortDate(entry.ts)}>
                  {relative(entry.ts)}
                  {entry.cost_usd ? ` · ${usd(entry.cost_usd)}` : ""}
                </p>
                {entry.via && (
                  <p className="text-xs text-ink-muted" data-testid="timeline-via">
                    {viaInWords(entry.via)}
                  </p>
                )}
              </li>
            ))}
          </ol>
          {timeline.data?.length === 0 && <Empty>Nothing to show</Empty>}
        </Card>
      </div>

      <TrackerCard
        slug={slug}
        tracker={(connectors.data ?? []).find((connector) => connector.kind === "tracker")}
        initiatedBy={data.initiated_by}
        url={data.url}
      />

      <Card title="Artifacts" action={<Provenance de="observed" />}>
        {artifacts.error ? (
          <p className="text-sm text-ink-muted">the artifacts cannot be read: {(artifacts.error as Error).message}</p>
        ) : artifacts.data ? (
          <Artifacts itemId={id} slug={slug} artifacts={artifacts.data} handOffs={passages.data ?? []} />
        ) : (
          <Empty>loading the artifacts…</Empty>
        )}
      </Card>

      {(passages.data ?? []).length > 0 && (
        <Card title="Hand-offs" action={<Provenance de="observed" />}>
          <Passages passages={passages.data ?? []} />
        </Card>
      )}

      {data.pr_url && (
        <Card title="Pull request">
          <a href={data.pr_url} target="_blank" rel="noreferrer" className="text-sm">
            {data.pr_url}
          </a>
        </Card>
      )}
    </div>
  );
}
