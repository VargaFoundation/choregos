// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { StopWithReason } from "@/components/activity/stop";
import { RunActivityView } from "@/components/run-activity";
import { Card, Empty, Input, ReadState, Select, StateBadge } from "@/components/ui";
import { elapsedMs, entryLabel, formatElapsed, spendSoFar, statusKind } from "@/lib/activity";
import { api, type ActivityFilter } from "@/lib/api";
import { installationId } from "@/lib/assistants";
import { assistantsApi } from "@/lib/assistants-api";
import { useLiveRefresh } from "@/lib/live";
import { runActivity } from "@/lib/run-activity";
import type { ActivityEntry } from "@/lib/types";

/** How often the list is read again while its stream is not open (the stream's fallback). */
export const ACTIVITY_POLL_MS = 10_000;

/** The current time, ticking every second: the elapsed times move while one watches. */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

const isActivity = (query: { queryKey: readonly unknown[] }) => query.queryKey[0] === "activity";

/**
 * What is running, then what has just finished (S64-06, ADR 0142 §1): runs, agent sessions and
 * conversation turns in one list, each with its agent, its model, the time it has run, what it has
 * spent so far and its status; a run opens onto its steps and can be stopped with a reason. The list
 * follows the organisation's activity stream and reads itself again every few seconds when the stream
 * is not open. `project` narrows it to one project (the project's own Activity section), `assistant` (an
 * installation id) starts it narrowed to one assistant's conversation turns. A turn says its assistant
 * and the person whose conversation it is; it has no stop of its own: its assistant's stop, on the
 * assistant's page, or its agent's, is the stop (ADR 0142 §4 bis).
 */
export function ActivityView({ org, project, assistant }: { org: string; project?: string | null; assistant?: string | null }) {
  const [filter, setFilter] = useState<Omit<ActivityFilter, "project">>(() => (assistant ? { assistant } : {}));
  const narrowed: ActivityFilter = { ...filter, ...(project ? { project } : {}) };
  const live = useLiveRefresh(api.activityStreamPath(org, narrowed), isActivity, ["activity.upsert", "activity.ended"]);
  const list = useQuery({
    queryKey: ["activity", org, narrowed],
    queryFn: () => api.activity(org, narrowed),
    refetchInterval: live ? false : ACTIVITY_POLL_MS,
  });
  const agents = useQuery({ queryKey: ["agents", org], queryFn: () => api.agents(org) });
  // The installations the reader manages name a turn's assistant; a reader who manages none sees its id.
  const installations = useQuery({ queryKey: ["installations", org], queryFn: () => assistantsApi.installations(org), retry: false });
  const names = new Map<string, string>();
  for (const item of installations.data?.items ?? []) {
    const id = installationId(item);
    if (id) names.set(id, item.display_name ?? item.slug);
  }
  const now = useNow();
  const set = (key: keyof typeof filter, value: string) =>
    setFilter((current) => ({ ...current, [key]: value || undefined }));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-4" role="group" aria-label="Filters">
        <label className="space-y-1 text-xs text-ink-muted">
          <span>Workflow</span>
          <Input aria-label="Workflow" className="w-40" value={filter.workflow ?? ""} onChange={(e) => set("workflow", e.target.value.trim())} />
        </label>
        <label className="space-y-1 text-xs text-ink-muted">
          <span>Agent</span>
          <Select aria-label="Agent" className="w-auto" value={filter.agent ?? ""} onChange={(e) => set("agent", e.target.value)}>
            <option value="">Every agent</option>
            {(agents.data ?? []).map((a) => (
              <option key={a.slug} value={a.slug}>
                {a.slug}
              </option>
            ))}
          </Select>
        </label>
        <label className="space-y-1 text-xs text-ink-muted">
          <span>Assistant</span>
          <Input
            aria-label="Assistant"
            className="w-40"
            placeholder="Installation id"
            value={filter.assistant ?? ""}
            onChange={(e) => set("assistant", e.target.value.trim())}
          />
        </label>
        <label className="space-y-1 text-xs text-ink-muted">
          <span>Kind</span>
          <Select aria-label="Kind" className="w-auto" value={filter.kind ?? ""} onChange={(e) => set("kind", e.target.value)}>
            <option value="">Everything</option>
            <option value="run">Runs</option>
            <option value="session">Sessions</option>
            <option value="turn">Conversation turns</option>
          </Select>
        </label>
        <p className="text-xs text-ink-muted" role="status" data-testid="live-state">
          {live ? "Live" : `Read every ${ACTIVITY_POLL_MS / 1000} s`}
        </p>
      </div>
      {!list.data ? (
        <ReadState lecture={list} quoi="what is running" />
      ) : (
        <>
          <EntryList
            title={`In flight (${list.data.in_flight.length})`}
            entries={list.data.in_flight}
            now={now}
            names={names}
            narrow={(id) => set("assistant", id)}
            inFlight
          />
          <EntryList
            title={`Finished (${list.data.finished.length})`}
            entries={list.data.finished}
            now={now}
            names={names}
            narrow={(id) => set("assistant", id)}
          />
        </>
      )}
    </div>
  );
}

/** How a row names the assistant of a turn, and narrows the list to it. */
type AssistantNaming = { names: Map<string, string>; narrow: (id: string) => void };

function EntryList({
  title,
  entries,
  now,
  names,
  narrow,
  inFlight = false,
}: AssistantNaming & {
  title: string;
  entries: ActivityEntry[];
  now: number;
  inFlight?: boolean;
}) {
  return (
    <Card title={title} padding="none" className="overflow-hidden" data-testid={inFlight ? "in-flight" : "finished"}>
      {entries.length === 0 ? (
        <div className="px-5 pb-5">
          <Empty>{inFlight ? "Nothing is running." : "Nothing ended in the last hour."}</Empty>
        </div>
      ) : (
        <ul className="divide-y divide-line border-t border-line">
          {entries.map((entry) => (
            <EntryRow key={`${entry.kind}-${entry.id}`} entry={entry} now={now} names={names} narrow={narrow} inFlight={inFlight} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function EntryRow({ entry, now, names, narrow, inFlight }: AssistantNaming & { entry: ActivityEntry; now: number; inFlight: boolean }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const spend = spendSoFar(entry);
  const label = entryLabel(entry);
  const href =
    entry.kind === "run" && entry.project_slug ? `/p/${encodeURIComponent(entry.project_slug)}/runs/${encodeURIComponent(entry.id)}` : null;
  const stepsId = `steps-${entry.kind}-${entry.id}`;
  return (
    <li data-testid="activity-entry" data-entry={entry.id} className="px-5 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {entry.kind === "run" ? (
          <button
            type="button"
            aria-expanded={open}
            aria-controls={stepsId}
            aria-label={`Steps of ${label}`}
            onClick={() => setOpen(!open)}
            className="text-ink-muted hover:text-ink"
          >
            {open ? <ChevronDown size={16} aria-hidden /> : <ChevronRight size={16} aria-hidden />}
          </button>
        ) : (
          <span className="w-4" aria-hidden />
        )}
        <span className="min-w-40 flex-1 font-medium" data-verbatim>
          {href ? (
            <Link href={href} className="hover:underline">
              {label}
            </Link>
          ) : (
            label
          )}
          {entry.project_slug && <span className="ml-2 text-xs font-normal text-ink-muted">{entry.project_slug}</span>}
          {entry.kind === "turn" && <TurnOf entry={entry} names={names} narrow={narrow} />}
        </span>
        <span className="text-ink-muted" data-verbatim title="Agent">
          {entry.agent_slug ?? "—"}
        </span>
        <span className="font-mono text-xs text-ink-muted" data-verbatim title="Model">
          {entry.model ?? "—"}
        </span>
        <span className="font-mono text-xs tabular-nums" data-testid="elapsed" title="Elapsed">
          {formatElapsed(elapsedMs(entry, now))}
        </span>
        <span className="font-mono text-xs tabular-nums text-ink-muted" data-testid="spend-tokens" title="Tokens so far">
          {spend.tokens}
        </span>
        <span className="font-mono text-xs tabular-nums" data-testid="spend-cost" title="Cost so far">
          {spend.cost}
        </span>
        <StateBadge state={entry.status} kind={statusKind(entry.status)} />
        {inFlight && entry.kind === "run" && entry.can_stop && (
          <StopWithReason
            question={
              <>
                Stop <strong data-verbatim>{label}</strong> now? The run is cancelled where it stands; its work item waits for a
                person, who reruns the stage, stops the work item or moves it on.
              </>
            }
            confirm="Stop the run"
            action={(reason) => api.cancelRun(entry.id, reason)}
            onDone={() => void client.invalidateQueries({ predicate: isActivity })}
          />
        )}
      </div>
      {open && entry.kind === "run" && (
        <div id={stepsId} className="mt-3 pl-8">
          <RunSteps runId={entry.id} />
        </div>
      )}
    </li>
  );
}

/**
 * Whose a conversation turn is: its assistant, named when the reader manages it (a click narrows the list
 * to that assistant's turns), and the person whose conversation it is.
 */
function TurnOf({ entry, names, narrow }: AssistantNaming & { entry: ActivityEntry }) {
  const assistant = entry.assistant ?? null;
  const name = assistant ? (names.get(assistant) ?? assistant) : null;
  return (
    <span className="ml-2 text-xs font-normal text-ink-muted" data-testid="turn-of">
      {name && assistant && (
        <>
          of{" "}
          <button
            type="button"
            className="underline-offset-2 hover:text-ink hover:underline"
            title="Only this assistant's turns"
            aria-label={`Only the turns of ${name}`}
            onClick={() => narrow(assistant)}
            data-verbatim
          >
            {name}
          </button>
        </>
      )}
      {entry.initiated_by && (
        <>
          {name ? ", " : ""}for <span data-verbatim>{entry.initiated_by}</span>
        </>
      )}
    </span>
  );
}

/** A run's steps: its events, read as the run page reads them (tool calls, refusals). */
function RunSteps({ runId }: { runId: string }) {
  const events = useQuery({ queryKey: ["activity-steps", runId], queryFn: () => api.runEvents(runId) });
  if (!events.data) return <ReadState lecture={events} quoi="the run's steps" />;
  if (events.data.length === 0) return <p className="text-xs text-ink-muted">No step recorded yet.</p>;
  return <RunActivityView activite={runActivity(events.data)} />;
}
