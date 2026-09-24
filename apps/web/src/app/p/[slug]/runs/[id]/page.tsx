"use client";

import { Heading } from "@varga/design-system";
import { useQuery } from "@tanstack/react-query";
import { use } from "react";
import { LiveLog } from "@/components/live-log";
import { Preuves } from "@/components/preuves";
import { Button, Card, Empty, ErrorNote, StateBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { duration, tokens, usd } from "@/lib/format";
import { useEventStream } from "@/lib/sse";
import { runEvents as mockRunEvents } from "@/mocks/data";
import type { RunEventDto } from "@/lib/types";

export default function RunPage({ params }: { params: Promise<{ slug: string; id: string }> }) {
  const { id } = use(params);
  const run = useQuery({ queryKey: ["run", id], queryFn: () => api.run(id) });
  const stored = useQuery({ queryKey: ["run-events", id], queryFn: () => api.runEvents(id) });
  const diff = useQuery({ queryKey: ["run-diff", id], queryFn: () => api.runDiff(id) });
  const live = useEventStream<RunEventDto>({
    path: `/runs/${id}/events`,
    enabled: run.data?.status === "running",
    mockEvents: mockRunEvents,
  });

  if (run.error) return <ErrorNote>{(run.error as Error).message}</ErrorNote>;
  if (!run.data) return <Empty>chargement…</Empty>;

  const events = dedupe([...(stored.data ?? []), ...live.events]);
  const evidence = run.data.result?.evidence;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Heading as="h2" size="lg">
            Run {run.data.stage_role} · tentative {run.data.attempt}
          </Heading>
          <p className="font-mono text-xs text-ink-muted">
            {run.data.backend} · {run.data.model} · exécuteur {run.data.executor_kind ?? "—"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StateBadge
            state={run.data.status}
            display={run.data.status}
            kind={run.data.status === "succeeded" ? "terminal" : run.data.status === "failed" ? "blocked" : "work"}
          />
          <Button
            onClick={() => {
              if (run.data?.work_item_id) void api.action(run.data.work_item_id, "rerun_stage");
            }}
          >
            Rejouer l&apos;étape
          </Button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-4">
        <Card title="coût">
          <p className="text-2xl">{usd(run.data.cost_usd)}</p>
          <p className="text-xs text-ink-muted">
            {tokens(run.data.tokens?.tokens_in)} entrants · {tokens(run.data.tokens?.tokens_out)} sortants ·{" "}
            {tokens(run.data.tokens?.tokens_cached)} en cache
          </p>
        </Card>
        <Card title="durée">
          <p className="text-2xl">{duration(run.data.tokens?.duration_s)}</p>
          <p className="text-xs text-ink-muted">{run.data.result?.diagnostics?.turns ?? 0} tours d&apos;agent</p>
        </Card>
        <Preuves evidence={evidence} />
        <Card title="périmètre">
          <ul className="space-y-1 font-mono text-xs text-ink-muted">
            {(run.data.allowed_paths ?? []).map((path) => (
              <li key={path}>{path}</li>
            ))}
          </ul>
          <p className="mt-2 text-xs">
            {run.data.result?.diagnostics?.permission_denials ?? 0} permission(s) refusée(s)
          </p>
        </Card>
      </div>

      <Card
        title="journal ACP"
        action={
          <span className="text-xs text-ink-muted">
            {live.connected ? "● en direct" : live.error ? live.error : "flux fermé"}
          </span>
        }
      >
        <LiveLog events={events} />
      </Card>

      <Card title="diff">
        {(diff.data?.files ?? []).length === 0 ? (
          <Empty>aucun fichier modifié</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>fichier</th>
                <th className="text-right">+</th>
                <th className="text-right">-</th>
                <th>périmètre</th>
              </tr>
            </thead>
            <tbody>
              {(diff.data?.files ?? []).map((file) => (
                <tr key={file.path}>
                  <td className="font-mono text-xs">{file.path}</td>
                  <td className="text-right text-ok">+{file.additions}</td>
                  <td className="text-right text-danger">−{file.deletions}</td>
                  <td>{file.in_scope === false ? <span className="text-danger">hors périmètre</span> : "✓"}</td>
                </tr>
              ))}
            </tbody>
          </table>
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
