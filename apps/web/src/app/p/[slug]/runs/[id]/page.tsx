// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { use, useState } from "react";
import { ActiviteDuRunVue } from "@/components/activite-du-run";
import { ContextReductionCard } from "@/components/context-reduction";
import { LiveLog } from "@/components/live-log";
import { PlanDeLAgent } from "@/components/plan-de-l-agent";
import { Provenance } from "@/components/provenance";
import { Acces } from "@/components/acces";
import { Garanties, verdictsDe } from "@/components/garanties";
import { Preuves } from "@/components/preuves";
import { Badge, Breadcrumbs, Card, Empty, ErrorNote, Heading, StateBadge, tabClasses } from "@/components/ui";
import { PodLogs } from "@/components/pod-logs";
import { api } from "@/lib/api";
import { duration, tokens, usd } from "@/lib/format";
import { activiteDuRun } from "@/lib/activite-du-run";
import { contextReductionOf } from "@/lib/context-reduction";
import { planDeLAgent } from "@/lib/plan-de-l-agent";
import { finDeclaree } from "@/lib/provenance";
import { useEventStream } from "@/lib/sse";
import type { RunEventDto } from "@/lib/types";
import { GesteConfirme } from "@/components/geste-confirme";

export default function RunPage({ params }: { params: Promise<{ slug: string; id: string }> }) {
  const { slug, id } = use(params);
  const run = useQuery({ queryKey: ["run", id], queryFn: () => api.run(id) });
  // Le ticket du run, pour le fil d'Ariane : même clé que sa page, une seule lecture.
  const idDuTicket = run.data?.work_item_id;
  const ticket = useQuery({
    queryKey: ["item", idDuTicket],
    queryFn: () => api.workItem(idDuTicket!),
    enabled: Boolean(idDuTicket),
  });
  const [relance, setRelance] = useState(false);
  const stored = useQuery({ queryKey: ["run-events", id], queryFn: () => api.runEvents(id) });
  const diff = useQuery({ queryKey: ["run-diff", id], queryFn: () => api.runDiff(id) });
  // Ce à quoi l'agent a touché, replié depuis le journal : les 200 événements bruts
  // d'un run ne se lisent pas, et un journal que personne ne lit n'est pas un audit.
  const acces = useQuery({ queryKey: ["run-access", id], queryFn: () => api.runAccess(id) });
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
  // Le plan que l'agent publie en ACP, révision après révision (S25-01) ; rien s'il n'en publie pas.
  const plan = planDeLAgent(events);
  // Ce que l'agent a appelé, relu du même journal (S25-02) : lisible, au-dessus du journal brut.
  const activite = activiteDuRun(events);
  // L'agent se dit fini ; la plateforme l'a-t-elle constaté (ADR 0045) ?
  const fin = finDeclaree(run.data.result?.status, verdictsDe(events));

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
          <Heading as="h2" size="lg">
            Run {run.data.stage_role} · attempt {run.data.attempt}
          </Heading>
          <p className="font-mono text-xs text-ink-muted" data-donnee>
            {run.data.backend} · {run.data.model} · executor {run.data.executor_kind ?? "—"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {fin && (
            <Badge tone="waiting" data-testid="fin-declaree" title="The agent says it is done; the platform has not observed it">
              {fin}
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
            <GesteConfirme
              ton="accent"
              question={`Replay this stage? An agent runs it again from the start — a new run, billed like this one (${usd(run.data.cost_usd)}).`}
              confirmer="Replay the stage"
              action={() => api.action(run.data!.work_item_id!, "rerun_stage")}
              onFait={() => setRelance(true)}
            >
              Replay the stage
            </GesteConfirme>
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
        <Preuves evidence={evidence} />
        <Card title="Scope" action={<Provenance de="observed" />}>
          <ul className="space-y-1 font-mono text-xs text-ink-muted">
            {(run.data.allowed_paths ?? []).map((path) => (
              <li key={path}>{path}</li>
            ))}
          </ul>
          <p className="mt-2 text-xs">{run.data.result?.diagnostics?.permission_denials ?? 0} permission(s) refused</p>
        </Card>
      </div>

      {plan && (
        <Card title="Agent plan" action={<Provenance de="declared" />}>
          <PlanDeLAgent plan={plan} />
        </Card>
      )}

      <Garanties events={events} />

      <ContextReductionCard reduction={contextReductionOf(events)} />

      <Acces acces={acces.data} />

      {(activite.appels.length > 0 || activite.refus.length > 0) && (
        <Card title="Activity" action={<Provenance de="declared" />}>
          <ActiviteDuRunVue activite={activite} />
        </Card>
      )}

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
        {(diff.data?.files ?? []).length === 0 ? (
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
