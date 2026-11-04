// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use, useState } from "react";
import { DecisionBar } from "@/components/decision-bar";
import { JourneyMap } from "@/components/journey/journey-map";
import { parked } from "@/components/journey/model";
import {
  ActorIcon,
  Breadcrumbs,
  Button,
  buttonClasses,
  Card,
  CostChip,
  Dot,
  Empty,
  ErrorNote,
  Heading,
  StateBadge,
  tonDActeur,
} from "@/components/ui";
import { api } from "@/lib/api";
import { libelleDeCode, relative, shortDate, tokens, usd } from "@/lib/format";
import type { WorkItemJourney } from "@/lib/types";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { AgentProposal } from "@/components/agent-proposal";
import { Artifacts } from "@/components/artifacts";
import { Passages } from "@/components/passages";
import { Provenance } from "@/components/provenance";
import { TrackerCard } from "@/components/who-writes";
import { viaInWords } from "@/lib/who-writes";
import { NextStepBanner } from "@/components/ticket/next-step-banner";
import { LongText, RequestCard } from "@/components/ticket/request-card";
import { WorkflowChip } from "@/components/workflow-chip";
import { fieldTitles } from "@/components/workflows/fields";
import { useWorkflowDefinition } from "@/components/workflows/use-workflow";
import { nextStep } from "@/lib/next-step";

/** While an agent runs or a person is awaited, the journey is re-read: the map follows. */
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
  // The hand-off receipts (S25-04): who produced each step output, who read it, under which fingerprint.
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
  // The titles its workflow gives the request's fields (S55-14); unread, the names stand in.
  const workflow = useWorkflowDefinition(slug, item.data?.workflow_name);

  if (item.error) return <ErrorNote>{(item.error as Error).message}</ErrorNote>;
  if (!item.data) return <Empty>Loading…</Empty>;
  const data = item.data;
  const next = nextStep(data, journey.data);

  async function control(action: string) {
    await api.action(id, action);
    await queryClient.invalidateQueries({ queryKey: ["item", id] });
  }

  return (
    <div className="space-y-4">
      <Breadcrumbs steps={[{ href: `/p/${slug}/board`, label: "Board" }, { label: data.tracker_key }]} />
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1.5">
          <Heading as="h1" size="xl" data-verbatim>
            {data.title}
          </Heading>
          {/* Which workflow, in its colour, and where the ticket stands in it (S55-05). */}
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {data.workflow_name && (
              <WorkflowChip
                name={data.workflow_name}
                version={data.workflow_version}
                href={`/p/${slug}/workflows/${encodeURIComponent(data.workflow_name)}`}
              />
            )}
            <StateBadge state={data.state} display={data.state_display} outcome={data.state_outcome} />
            <span className="font-mono text-ink-muted" data-verbatim>
              {data.tracker_key}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <CostChip costUsd={data.totals?.cost_usd} tokensIn={data.totals?.tokens_in} />
          <Button
            onClick={() =>
              void control(data.paused ? "resume" : "pause").catch((cause: unknown) =>
                setErreurDeControle(cause instanceof Error ? cause.message : "refused"),
              )
            }
          >
            {data.paused ? "Resume" : "Pause"}
          </Button>
          <ConfirmedGesture
            tonDuBouton="danger"
            question="Stop this ticket? Its run is cancelled and it leaves the workflow — it does not resume, it has to be filed again."
            confirmer="Stop the ticket"
            action={() => control("stop")}
          >
            Stop
          </ConfirmedGesture>
        </div>
      </div>

      <NextStepBanner
        step={next}
        action={
          next.action?.kind === "decide" ? (
            <a href="#decision" className={buttonClasses("accent", "sm")}>
              {next.action.label}
            </a>
          ) : next.action?.kind === "replay" || parked(journey.data) ? (
            <ConfirmedGesture
              ton="accent"
              question="Replay the stage that escalated? An agent runs it again from the start — a new run, billed."
              confirmer="Replay the stage"
              action={() => control("rerun_stage")}
            >
              Replay the stage
            </ConfirmedGesture>
          ) : undefined
        }
      />

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

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="min-w-0 space-y-4 lg:col-span-2">
          {data.pending_request && (
            <Card title="Decision awaited" id="decision" className="scroll-mt-16 border-l-4 border-l-waiting">
              {typeof data.pending_request.payload?.title === "string" && (
                <p className="mb-2 text-sm font-medium" data-testid="decision-title">
                  {data.pending_request.payload.title}
                </p>
              )}
              {(data.pending_request.payload?.question ?? data.pending_request.payload?.summary) ? (
                // Markdown, folded past a screenful — it was one raw paragraph (S55-05).
                <div className="mb-3" data-testid="decision-text">
                  <LongText text={String(data.pending_request.payload?.question ?? data.pending_request.payload?.summary)} />
                </div>
              ) : null}
              {/* What the agent proposes is read here, before deciding, not in the Artifacts card below. */}
              <div className="mb-3">
                <AgentProposal slug={slug} runs={runs.data} prUrl={data.pr_url} />
              </div>
              <DecisionBar
                itemId={id}
                kind={data.pending_request.kind}
                request={data.pending_request}
                onDone={() => queryClient.invalidateQueries({ queryKey: ["item", id] })}
              />
            </Card>
          )}
          <RequestCard
            body={data.body_snapshot}
            fields={data.fields}
            titles={fieldTitles(workflow.data?.json?.metadata?.inputs)}
          />
        </div>
        <Card title="About this ticket" data-testid="ticket-facts" className="self-start">
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
            <dt className="text-ink-muted">Filed</dt>
            <dd title={shortDate(data.created_at)}>{relative(data.created_at)}</dd>
            {data.closed_at && (
              <>
                <dt className="text-ink-muted">Closed</dt>
                <dd title={shortDate(data.closed_at)}>{relative(data.closed_at)}</dd>
              </>
            )}
            <dt className="text-ink-muted">Spent</dt>
            <dd>
              {usd(data.totals?.cost_usd)}
              {data.estimate?.median_usd ? (
                <span className="text-ink-muted"> of about {usd(data.estimate.median_usd)}</span>
              ) : null}
            </dd>
            {data.url && (
              <>
                <dt className="text-ink-muted">Issue</dt>
                <dd className="min-w-0 truncate">
                  <a href={data.url} target="_blank" rel="noreferrer">
                    {data.tracker_key}
                  </a>
                </dd>
              </>
            )}
            {data.pr_url && (
              <>
                <dt className="text-ink-muted">Pull request</dt>
                <dd className="min-w-0 truncate">
                  <a href={data.pr_url} target="_blank" rel="noreferrer">
                    {data.pr_url.replace(/^https?:\/\//, "")}
                  </a>
                </dd>
              </>
            )}
          </dl>
        </Card>
      </div>

      <Card title="Journey">
        {journey.data ? (
          <JourneyMap
            journey={journey.data}
            slug={slug}
            itemId={id}
            decisionAnchor={data.pending_request ? "decision" : undefined}
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

      <Heading as="h3" size="md" className="pt-2">
        History and outputs
      </Heading>
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
          {/* A rail, and on it a dot per moment, in the colour of whoever acted (S24-04). */}
          <ol className="relative space-y-3 before:absolute before:top-2 before:bottom-2 before:left-[3.5px] before:w-px before:bg-line">
            {(timeline.data ?? []).map((entry, index) => (
              <li key={`${entry.ts}-${index}`} className="relative pl-5">
                <Dot tone={tonDActeur(entry.actor_kind ?? "system")} size={8} className="absolute top-1.5 left-0" />
                <p className="flex items-center gap-2 text-sm">
                  <ActorIcon kind={entry.actor_kind ?? "system"} name={entry.actor ?? undefined} point={false} />
                  <span>{entry.title}</span>
                </p>
                {entry.detail && (
                  <p className="text-xs text-ink-muted" data-verbatim>
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
        reporter={data.reporter}
        url={data.url}
        fields={data.fields}
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

    </div>
  );
}
