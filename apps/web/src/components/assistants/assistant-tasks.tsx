// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, Input, Select } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { assistantsApi, type Delegation, type TaskSubscription } from "@/lib/assistants-api";
import { relative } from "@/lib/format";

type Ref = { org: string; slug: string };
type Decl = { name: string; delegation: string; schedulable: boolean };

async function subscription(ref: Ref, task: string): Promise<TaskSubscription | null> {
  try {
    return await assistantsApi.task(ref.org, ref.slug, task);
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) return null;
    throw cause;
  }
}

function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

type NeedShown = { need: string; operations: string[] };

/** The needs a delegation covers, as one line. */
function needsOf(delegation: Delegation): string {
  return delegation.needs.map((n) => `${n.need} (${n.operations.join(", ")})`).join("; ");
}

/**
 * The standing delegation of a task that reads on the person's behalf: what it covers, until when, and the
 * two gestures: agree to the exact list of needs the API shows, and revoke at once.
 */
function DelegationControls({ org, slug, task, subscribed }: Ref & { task: string; subscribed: boolean }) {
  const client = useQueryClient();
  const key = ["assistant-delegations"];
  const all = useQuery({ queryKey: key, queryFn: () => assistantsApi.delegations() });
  const current = (all.data?.items ?? []).find(
    (d) => d.org === org && d.assistant === slug && d.task === task && d.status !== "revoked",
  );
  const [needs, setNeeds] = useState<NeedShown[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function agree(agreeTo: string[]) {
    setError(null);
    try {
      await assistantsApi.agreeDelegation({ org, assistant: slug, task, agree_to: agreeTo });
      setNeeds(null);
      await client.invalidateQueries({ queryKey: key });
    } catch (cause) {
      // The 409 of a first agreement carries the exact needs: they are shown, then agreed to.
      const extra = cause instanceof ApiError ? (cause.problem as unknown as { needs?: NeedShown[] }) : null;
      if (extra?.needs && agreeTo.length === 0) {
        setNeeds(extra.needs);
        return;
      }
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  }
  async function revoke(id: string) {
    setError(null);
    try {
      await assistantsApi.revokeDelegation(id);
      await client.invalidateQueries({ queryKey: key });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  }

  return (
    <div className="space-y-2 rounded border border-line p-2 text-sm" aria-label="Delegation">
      {current ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={current.status === "active" ? "succeeded" : "waiting"}>{current.status}</Badge>
            <span className="text-ink-muted">Reads {needsOf(current)} until {new Date(current.expires_at).toLocaleDateString()}</span>
          </div>
          {current.suspended_reason && <ErrorNote>{current.suspended_reason}</ErrorNote>}
          <div className="flex flex-wrap gap-2">
            {current.status !== "active" && (
              <Button size="sm" tone="default" onClick={() => void agree(current.needs.map((n) => n.need))}>
                Agree again
              </Button>
            )}
            <Button size="sm" tone="danger" onClick={() => void revoke(current.id)}>
              Revoke the delegation
            </Button>
          </div>
        </>
      ) : needs ? (
        <>
          <p>
            Agreeing lets this task read, while you are away: <strong>{needs.map((n) => `${n.need} (${n.operations.join(", ")})`).join("; ")}</strong>. Read only; it ends
            after 90 days unless the installation sets less.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" tone="primary" onClick={() => void agree(needs.map((n) => n.need))}>
              I agree to these reads
            </Button>
            <Button size="sm" tone="default" onClick={() => setNeeds(null)}>
              Cancel
            </Button>
          </div>
        </>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-ink-muted">No standing delegation: its scheduled runs are refused.</span>
          <Button size="sm" tone="default" disabled={!subscribed} onClick={() => void agree([])}>
            Review and agree
          </Button>
        </div>
      )}
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}

function TaskCard({ org, slug, task }: Ref & { task: Decl }) {
  const client = useQueryClient();
  const key = ["assistant-task", org, slug, task.name];
  const current = useQuery({ queryKey: key, queryFn: () => subscription({ org, slug }, task.name) });
  const sub = current.data;
  const [kind, setKind] = useState<"on_demand" | "schedule" | "poll">("on_demand");
  const [cron, setCron] = useState("");
  const [timezone, setTimezone] = useState(browserZone());
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(work: () => Promise<string | void>) {
    setError(null);
    setMessage(null);
    try {
      const said = await work();
      if (said) setMessage(said);
      await client.invalidateQueries({ queryKey: key });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  }

  const effectiveKind = sub?.trigger.kind ?? kind;
  return (
    <Card title={task.name.replaceAll("_", " ")} padding="sm">
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {sub ? (
            <>
              <Badge tone={sub.enabled ? "succeeded" : "neutral"}>{sub.enabled ? "Subscribed" : "Paused"}</Badge>
              <span className="text-ink-muted">
                {sub.trigger.kind === "on_demand" ? "On demand" : `${sub.trigger.cron} (${sub.trigger.timezone})`}
              </span>
              {sub.last_run_at && <span className="text-xs text-ink-muted">last run {relative(sub.last_run_at)}</span>}
            </>
          ) : (
            <Badge tone="neutral">Not subscribed</Badge>
          )}
          <span className="text-xs text-ink-muted">
            Delegation: {task.delegation === "read_only" ? "reads on your behalf when you are away" : "none"}
          </span>
        </div>
        {task.delegation === "read_only" && (
          <DelegationControls org={org} slug={slug} task={task.name} subscribed={!!sub} />
        )}
        {sub?.last_refusal && <ErrorNote>The last run was refused: {sub.last_refusal}</ErrorNote>}
        {!sub && (
          <div className="flex flex-wrap items-end gap-2">
            <label className="space-y-1 text-xs text-ink-muted">
              Runs
              <Select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
                <option value="on_demand">On demand</option>
                {task.schedulable && <option value="schedule">On a schedule</option>}
              </Select>
            </label>
            {effectiveKind === "schedule" && (
              <>
                <label className="space-y-1 text-xs text-ink-muted">
                  Cron
                  <Input value={cron} onChange={(e) => setCron(e.target.value)} placeholder="30 7 * * 1-5" />
                </label>
                <label className="space-y-1 text-xs text-ink-muted">
                  Time zone
                  <Input value={timezone} onChange={(e) => setTimezone(e.target.value)} />
                </label>
              </>
            )}
          </div>
        )}
        {message && <p className="text-sm text-ink-muted">{message}</p>}
        {error && <ErrorNote>{error}</ErrorNote>}
        <div className="flex flex-wrap gap-2">
          {!sub ? (
            <Button
              tone="primary"
              size="sm"
              onClick={() =>
                void run(async () => {
                  await assistantsApi.putTask(org, slug, task.name, {
                    kind,
                    cron: kind === "schedule" ? cron : null,
                    timezone: kind === "schedule" ? timezone : null,
                    enabled: true,
                  });
                })
              }
            >
              Subscribe
            </Button>
          ) : (
            <>
              <Button
                size="sm"
                tone="default"
                onClick={() => void run(async () => void (await assistantsApi.putTask(org, slug, task.name, { enabled: !sub.enabled })))}
              >
                {sub.enabled ? "Pause" : "Resume"}
              </Button>
              <Button
                size="sm"
                tone="danger"
                onClick={() => void run(() => assistantsApi.deleteTask(org, slug, task.name))}
              >
                Unsubscribe
              </Button>
            </>
          )}
          <Button
            size="sm"
            tone="default"
            onClick={() =>
              void run(async () => {
                const started = await assistantsApi.runTask(org, slug, task.name);
                return `Started: ${started.key}. The result lands in your inbox.`;
              })
            }
          >
            Run now
          </Button>
        </div>
      </div>
    </Card>
  );
}

/** The tasks an assistant offers: subscribe on a cron and a time zone, run now, unsubscribe. */
export function AssistantTasks({ org, slug, tasks }: Ref & { tasks: Decl[] }) {
  if (tasks.length === 0) return <Empty title="No task">This assistant offers no task.</Empty>;
  return (
    <div className="space-y-3">
      {tasks.map((t) => (
        <TaskCard key={t.name} org={org} slug={slug} task={t} />
      ))}
    </div>
  );
}
