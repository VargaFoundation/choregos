// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { use, useState } from "react";
import { RunActivityView } from "@/components/run-activity";
import { RunScriptsCard } from "@/components/run-scripts";
import { ContextReductionCard } from "@/components/context-reduction";
import { RunContextCard } from "@/components/context-breakdown";
import { LiveLog } from "@/components/live-log";
import { AgentPlanView } from "@/components/agent-plan";
import { Provenance } from "@/components/provenance";
import { StoredDiff } from "@/components/diff-view";
import { Access } from "@/components/access";
import { Guarantees, verdictsDe } from "@/components/guarantees";
import { EvidencePanel } from "@/components/evidence";
import { Badge, Breadcrumbs, Card, Empty, ErrorNote, Heading, StateBadge, tabClasses } from "@/components/ui";
import { PodLogs } from "@/components/pod-logs";
import { api } from "@/lib/api";
import { duration, tokens, usd } from "@/lib/format";
import { runActivity } from "@/lib/run-activity";
import { contextReductionOf } from "@/lib/context-reduction";
import { runScriptsOf } from "@/lib/run-scripts";
import { RemoteTaskCard } from "@/components/remote/remote-task";
import { remoteTask } from "@/lib/remote-run";
import { agentPlan } from "@/lib/agent-plan";
import { declaredEnd } from "@/lib/provenance";
import { useEventStream } from "@/lib/sse";
import type { RunEventDto } from "@/lib/types";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { RunIdentity } from "@/components/who-writes";

export default function RunPage({ params }: { params: Promise<{ slug: string; id: string }> }) {
  const { slug, id } = use(params);
  const run = useQuery({ queryKey: ["run", id], queryFn: () => api.run(id) });
  // The run's ticket, for the breadcrumb: same key as its page, a single read.
  const idDuTicket = run.data?.work_item_id;
  const ticket = useQuery({
    queryKey: ["item", idDuTicket],
    queryFn: () => api.workItem(idDuTicket!),
    enabled: Boolean(idDuTicket),
  });
  const [relance, setRelance] = useState(false);
  const stored = useQuery({ queryKey: ["run-events", id], queryFn: () => api.runEvents(id) });
  const diff = useQuery({ queryKey: ["run-diff", id], queryFn: () => api.runDiff(id) });
  // The patch this run left, when it changed the branch (ADR 0129 §7); a run that did not has only its counters.
  const artifacts = useQuery({
    queryKey: ["artifacts", idDuTicket],
    queryFn: () => api.artifacts(idDuTicket!),
    enabled: Boolean(idDuTicket),
  });
  const patch = artifacts.data?.find((a) => a.type === "diff" && a.name === "diff" && a.produced_by.run_id === id);
  // What the agent touched, folded from the journal: a run's 200 raw events
  // are not readable, and a journal nobody reads is not an audit.
  const access = useQuery({ queryKey: ["run-access", id], queryFn: () => api.runAccess(id) });
  // What fills the model's context, call after call (ADR 0141 §6): refreshed while the run goes on.
  const context = useQuery({
    queryKey: ["run-context", id],
    queryFn: () => api.runContext(id),
    refetchInterval: run.data?.status === "running" ? 10_000 : false,
  });
  const transcript = useQuery({
    queryKey: ["run-transcript", id],
    queryFn: () => api.transcript(id),
    enabled: run.data?.status === "succeeded" || run.data?.status === "failed",
    retry: false,
  });
  // The journal says what the agent did; the pod logs say what happened to the pod (S36-03).
  const [output, setOutput] = useState<"journal" | "logs">("journal");
  const podLogs = useQuery({
    queryKey: ["run-logs", id],
    queryFn: () => api.runLogs(id),
    enabled: output === "logs",
    // While the pod runs, the orchestrator refreshes the tail at each poll: follow it.
    refetchInterval: (query) => (query.state.data?.source === "live" ? 10_000 : false),
  });
  const live = useEventStream<RunEventDto>({
    path: `/runs/${id}/events`,
    enabled: run.data?.status === "running",
    mockEvents: () => import("@/mocks/data").then((fixtures) => fixtures.runEvents),
  });

  if (run.error) return <ErrorNote>{(run.error as Error).message}</ErrorNote>;
  if (!run.data) return <Empty>Loading…</Empty>;

  const events = dedupe([...(stored.data ?? []), ...live.events]);
  const evidence = run.data.result?.evidence;
  // The plan the agent publishes over ACP, revision after revision (S25-01); nothing if it publishes none.
  const plan = agentPlan(events);
  // What the agent called, re-read from the same journal (S25-02): readable, above the raw journal.
  const activity = runActivity(events);
  // What the remote agent did, when the step called one (ADR 0062).
  const remote = remoteTask(events);
  // The agent says it is done; did the platform verify it (ADR 0045)?
  const end = declaredEnd(run.data.result?.status, verdictsDe(events));

  return (
    <div className="space-y-4">
      <Breadcrumbs
        steps={[
          { href: `/p/${slug}/board`, label: "Board" },
          ...(idDuTicket
            ? [{ href: `/p/${slug}/items/${idDuTicket}`, label: ticket.data?.tracker_key ?? "ticket" }]
            : []),
          { label: `Run ${run.data.stage_role} · attempt ${run.data.attempt}` },
        ]}
      />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Heading as="h1" size="xl">
            Run {run.data.stage_role} · attempt {run.data.attempt}
          </Heading>
          <p className="font-mono text-xs text-ink-muted" data-verbatim>
            {run.data.backend} · {run.data.model} · executor {run.data.executor_kind ?? "—"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {end && (
            <Badge tone="waiting" data-testid="declared-end" title="The agent says it is done; the platform has not observed it">
              {end}
            </Badge>
          )}
          <StateBadge
            state={run.data.status}
            display={run.data.status}
            kind={run.data.status === "succeeded" ? "terminal" : run.data.status === "failed" ? "blocked" : "work"}
          />
          {transcript.data?.url && (
            <a
              href={transcript.data.url}
              className="text-xs text-ink-muted hover:text-ink"
              target="_blank"
              rel="noreferrer"
            >
              Full transcript
            </a>
          )}
          {run.data.work_item_id && (
            <ConfirmedGesture
              ton="accent"
              question={`Replay this stage? An agent runs it again from the start — a new run, billed like this one (${usd(run.data.cost_usd)}).`}
              confirmer="Replay the stage"
              action={() => api.action(run.data!.work_item_id!, "rerun_stage")}
              onDone={() => setRelance(true)}
            >
              Replay the stage
            </ConfirmedGesture>
          )}
          {relance && (
            <span className="text-xs" role="status">
              Replay asked — the new run appears on the ticket
            </span>
          )}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-4">
        <Card title="Cost" action={<Provenance de="observed" />}>
          <p className="text-2xl">{usd(run.data.cost_usd)}</p>
          <p className="text-xs text-ink-muted">
            {tokens(run.data.tokens?.tokens_in)} in · {tokens(run.data.tokens?.tokens_out)} out ·{" "}
            {tokens(run.data.tokens?.tokens_cached)} cached
          </p>
        </Card>
        <Card title="Duration" action={<Provenance de="observed" />}>
          <p className="text-2xl">{duration(run.data.tokens?.duration_s)}</p>
          <p className="text-xs text-ink-muted">{run.data.result?.diagnostics?.turns ?? 0} agent turns</p>
        </Card>
        <EvidencePanel evidence={evidence} />
        <Card title="Scope" action={<Provenance de="observed" />}>
          <ul className="space-y-1 font-mono text-xs text-ink-muted">
            {(run.data.allowed_paths ?? []).map((path) => (
              <li key={path}>{path}</li>
            ))}
          </ul>
          <p className="mt-2 text-xs">{run.data.result?.diagnostics?.permission_denials ?? 0} permission(s) refused</p>
        </Card>
      </div>

      <RunIdentity run={run.data} chain={access.data?.chain} />

      {remote && <RemoteTaskCard task={remote} />}

      {plan && (
        <Card title="Agent plan" action={<Provenance de="declared" />}>
          <AgentPlanView plan={plan} />
        </Card>
      )}

      <Guarantees events={events} />

      <RunContextCard context={context.data} />

      <ContextReductionCard reduction={contextReductionOf(events)} />

      <Access acces={access.data} />

      {(activity.calls.length > 0 || activity.refusals.length > 0) && (
        <Card title="Activity" action={<Provenance de="declared" />}>
          <RunActivityView activite={activity} />
        </Card>
      )}

      {/* The calls a script made through the tool bridge, under their script (ADR 0122 §4, S57-10). */}
      <RunScriptsCard scripts={runScriptsOf(events)} slug={slug} />

      <Card
        title={
          <div role="tablist" aria-label="run output" className="flex gap-4">
            {(
              [
                ["journal", "ACP journal"],
                ["logs", "Pod logs"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="tab"
                id={`run-output-${key}`}
                aria-selected={output === key}
                aria-controls="run-output-panel"
                onClick={() => setOutput(key)}
                className={tabClasses(output === key)}
              >
                {label}
              </button>
            ))}
          </div>
        }
        action={
          output === "journal" ? (
            <span className="text-xs text-ink-muted">
              {live.connected ? "● Live" : live.error ? live.error : "Stream closed"}
            </span>
          ) : undefined
        }
      >
        <div role="tabpanel" id="run-output-panel" aria-labelledby={`run-output-${output}`}>
          {output === "journal" ? (
            <LiveLog events={events} />
          ) : podLogs.error ? (
            <ErrorNote>{(podLogs.error as Error).message}</ErrorNote>
          ) : (
            <PodLogs logs={podLogs.data} />
          )}
        </div>
      </Card>

      <Card title="Diff" action={<Provenance de="observed" />}>
        {patch && idDuTicket ? (
          <StoredDiff
            itemId={idDuTicket}
            artifact={patch}
            isOutOfScope={(path) => diff.data?.files.find((f) => f.path === path)?.in_scope === false}
          />
        ) : (diff.data?.files ?? []).length === 0 ? (
          <Empty>No file changed</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>File</th>
                  <th className="text-right">+</th>
                  <th className="text-right">-</th>
                  <th>Scope</th>
                </tr>
              </thead>
              <tbody>
                {(diff.data?.files ?? []).map((file) => (
                  <tr key={file.path}>
                    <td className="font-mono text-xs">{file.path}</td>
                    <td className="text-right text-ok">+{file.additions}</td>
                    <td className="text-right text-danger">−{file.deletions}</td>
                    <td>{file.in_scope === false ? <span className="text-danger">Out of scope</span> : "✓"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function dedupe(events: RunEventDto[]): RunEventDto[] {
  const seen = new Map<number, RunEventDto>();
  for (const event of events) seen.set(event.seq, event);
  return [...seen.values()].sort((a, b) => a.seq - b.seq);
}
