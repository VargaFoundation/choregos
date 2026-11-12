// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, ReadState, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { assistantsApi, type TaskEventLine } from "@/lib/assistants-api";
import { shortDate } from "@/lib/format";

type Ref = { org: string; slug: string };

const OUTCOME_TONE = { started: "succeeded", duplicate: "neutral", refused: "failed" } as const;

/** One copyable value, selectable too. */
function Copyable({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="w-12 text-ink-muted">{label}</span>
      <code data-verbatim className="break-all">
        {value}
      </code>
      <Button size="sm" aria-label={`Copy the ${label.toLowerCase()}`} onClick={() => void copy()}>
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

/** The URL and the key of the event door of a `trigger` task: a secret, shown on each click, kept nowhere. */
export function TaskEventKeyCard({ org, slug }: Ref) {
  const [task, setTask] = useState("");
  const [shown, setShown] = useState<{ task: string; url: string; secret: string; generation: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function reveal() {
    setError(null);
    setShown(null);
    try {
      const got = await assistantsApi.taskEventKey(org, slug, task.trim());
      setShown({ task: task.trim(), ...got });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  }
  return (
    <Card title="Task event door" eyebrow="Secret">
      <div className="space-y-3">
        <p className="text-sm text-ink-muted">
          A task with a <code>trigger</code> starts when a system posts a signed event to its URL. The key below is a secret: anyone who
          holds it can start this task for any subscriber. Each showing is in the audit log (never the key). Do not paste it in a
          ticket or a chat.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Task">{(p) => <Input {...p} value={task} onChange={(e) => setTask(e.target.value)} placeholder="new_ticket" />}</Field>
          <Button size="sm" disabled={task.trim() === ""} onClick={() => void reveal()}>
            Show the URL and key
          </Button>
        </div>
        {error && <ErrorNote>{error}</ErrorNote>}
        {shown && (
          <div className="space-y-2" data-testid="event-key">
            <Copyable label="URL" value={shown.url} />
            <Copyable label="Key" value={shown.secret} />
            <p className="text-xs text-ink-muted">
              Task {shown.task}, generation {shown.generation}. Sign each body with HMAC-SHA256 in <code>X-Choregos-Signature</code>.
            </p>
          </div>
        )}
      </div>
    </Card>
  );
}

/** The events the installation's `trigger` tasks received, newest first: what was decided, never a payload. */
export function TaskEventJournal({ org, slug }: Ref) {
  const events = useQuery({ queryKey: ["task-events", org, slug], queryFn: () => assistantsApi.taskEvents(org, slug) });
  return (
    <Card title="Task events" eyebrow="Journal">
      <div className="space-y-3">
        <ReadState lecture={events} quoi="the task events" />
        {events.data && events.data.items.length === 0 && <Empty>No event received.</Empty>}
        {events.data && events.data.items.length > 0 && (
          <Table>
            <THead>
              <TR>
                <TH>When</TH>
                <TH>Task</TH>
                <TH>Outcome</TH>
                <TH>Summary</TH>
                <TH>Work item</TH>
              </TR>
            </THead>
            <TBody>
              {events.data.items.map((line: TaskEventLine) => (
                <TR key={`${line.task}-${line.event_id}-${line.received_at}`}>
                  <TD>{shortDate(line.received_at)}</TD>
                  <TD>{line.task}</TD>
                  <TD>
                    <Badge tone={OUTCOME_TONE[line.outcome]}>{line.outcome}</Badge>
                  </TD>
                  <TD>{line.summary}</TD>
                  <TD>{line.work_item_key ?? "—"}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </div>
    </Card>
  );
}

/** The Teams webhook of the deliveries: configured or not, its version; the URL is sealed and never shown. */
export function TeamsDeliveryCard({ org, slug }: Ref) {
  const client = useQueryClient();
  const key = ["teams-delivery", org, slug];
  const state = useQuery({ queryKey: key, queryFn: () => assistantsApi.teamsDelivery(org, slug) });
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function seal() {
    setError(null);
    try {
      await assistantsApi.putTeamsDelivery(org, slug, url.trim());
      setUrl("");
      await client.invalidateQueries({ queryKey: key });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  }
  return (
    <Card title="Microsoft Teams" eyebrow="Deliveries">
      <div className="space-y-3">
        <ReadState lecture={state} quoi="the Teams delivery" />
        {state.data && (
          <p className="text-sm">
            {state.data.configured ? (
              <>
                <Badge tone="succeeded">Configured</Badge> version {state.data.version}
              </>
            ) : (
              <Badge tone="neutral">Not configured</Badge>
            )}
          </p>
        )}
        <p className="text-xs text-ink-muted">
          The webhook URL is a credential: it is sealed in the organisation&apos;s secret store and never shown again.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Teams webhook URL">
            {(p) => <Input {...p} type="password" autoComplete="off" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" />}
          </Field>
          <Button size="sm" tone="primary" disabled={url.trim() === ""} onClick={() => void seal()}>
            Seal the URL
          </Button>
          {state.data?.configured && (
            <ConfirmedGesture
              question="Remove the Teams channel? The sealed URL is deleted."
              confirmer="Remove"
              action={async () => {
                await assistantsApi.deleteTeamsDelivery(org, slug);
                await client.invalidateQueries({ queryKey: key });
              }}
            >
              Remove
            </ConfirmedGesture>
          )}
        </div>
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Card>
  );
}
