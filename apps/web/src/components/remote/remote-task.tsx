// SPDX-License-Identifier: Apache-2.0
"use client";

import { Alert, Badge, Card } from "@/components/ui";
import { duration, shortDate } from "@/lib/format";
import { remoteReasonText, shortDigest, type RemoteTask } from "@/lib/remote-run";

/**
 * The remote agent's side of a run (ADR 0062): where the step went, the task it created, the states
 * the task went through and how it ended. What a remote agent says of itself is declared, never
 * measured; the card and the digest are what the platform checked.
 */
export function RemoteTaskCard({ task }: { task: RemoteTask }) {
  const failed = task.reason !== null;
  return (
    <Card
      title="Remote agent"
      action={<Badge tone="accent">Remote · A2A</Badge>}
      data-testid="remote-task"
    >
      <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-ink-muted">Endpoint</dt>
          <dd className="break-all font-mono" data-donnee>
            {task.endpoint || "—"}
          </dd>
        </div>
        <div>
          <dt className="text-ink-muted">Skill</dt>
          <dd className="font-mono">{task.skill ?? "any"}</dd>
        </div>
        <div>
          <dt className="text-ink-muted">Task</dt>
          <dd className="break-all font-mono" data-testid="remote-task-id">
            {task.taskId ?? "none: nothing was sent"}
          </dd>
        </div>
        <div>
          <dt className="text-ink-muted">Card</dt>
          <dd className="font-mono" title={task.cardDigest ?? undefined}>
            {shortDigest(task.cardDigest)}
            {task.protocolVersion ? ` · A2A ${task.protocolVersion}` : ""}
          </dd>
        </div>
      </dl>
      {task.states.length > 0 && (
        <ol className="mt-3 flex flex-wrap items-center gap-2 text-xs" aria-label="Task states" data-testid="remote-states">
          {task.states.map((step, index) => (
            <li key={`${step.state}-${index}`} className="flex items-center gap-2">
              {index > 0 && <span aria-hidden="true" className="text-ink-subtle">→</span>}
              <span className="rounded border border-line px-2 py-0.5">
                {step.state}
                <span className="ml-1 text-ink-subtle">{shortDate(step.ts)}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
      {task.finalState && (
        <p className="mt-3 text-xs text-ink-muted" data-testid="remote-final">
          Ended <span className="font-medium text-ink">{task.finalState}</span>
          {task.durationS !== null ? ` after ${duration(task.durationS)}` : ""}
        </p>
      )}
      {failed && (
        <div className="mt-3" data-testid="remote-reason">
          <Alert tone="warn">{remoteReasonText(task.reason)}</Alert>
        </div>
      )}
      {task.cardChanged && (
        <p className="mt-2 font-mono text-xs text-ink-muted" data-testid="remote-drift">
          Pinned {shortDigest(task.cardChanged.pinned)} · now {shortDigest(task.cardChanged.now)}
        </p>
      )}
    </Card>
  );
}
