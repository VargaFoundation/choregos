// SPDX-License-Identifier: Apache-2.0
/**
 * What a remote agent did during a run (ADR 0062), read from the `remote.*` events the runner
 * journals: it started (endpoint, skill, the card's digest), its task changed state, it finished —
 * or its card had changed and nothing was sent. A run with none of these events is not a remote run.
 */
import type { RunEventDto } from "./types";

export type RemoteStep = { state: string; ts: string };

export type RemoteTask = {
  endpoint: string;
  skill: string | null;
  cardDigest: string | null;
  protocolVersion: string | null;
  taskId: string | null;
  states: RemoteStep[];
  /** The final state the runner journaled, `error` when it never got an answer, `timeout` when it cut. */
  finalState: string | null;
  durationS: number | null;
  /** The reason a failed run carries: `remote_failed`, `remote_timeout`, `remote_card_changed`… */
  reason: string | null;
  /** The card's digest now, when it was not the one the version pinned. */
  cardChanged: { pinned: string; now: string } | null;
};

const text = (value: unknown): string | null => (typeof value === "string" && value ? value : null);

/** The remote task of a run, or `null` when the run called no remote agent. */
export function remoteTask(events: RunEventDto[]): RemoteTask | null {
  const bySeq = new Map<number, RunEventDto>();
  for (const event of events) bySeq.set(event.seq, event);
  let task: RemoteTask | null = null;
  const start = (): RemoteTask =>
    (task ??= {
      endpoint: "",
      skill: null,
      cardDigest: null,
      protocolVersion: null,
      taskId: null,
      states: [],
      finalState: null,
      durationS: null,
      reason: null,
      cardChanged: null,
    });
  for (const event of [...bySeq.values()].sort((a, b) => a.seq - b.seq)) {
    const payload = (event.payload ?? {}) as Record<string, unknown>;
    if (event.type === "remote.started") {
      const t = start();
      t.endpoint = text(payload.endpoint) ?? t.endpoint;
      t.skill = text(payload.skill);
      t.cardDigest = text(payload.card_digest);
      t.protocolVersion = text(payload.protocol_version);
    } else if (event.type === "remote.update") {
      const t = start();
      const state = text(payload.state);
      if (state && t.states.at(-1)?.state !== state) t.states.push({ state, ts: event.ts });
      t.taskId = text(payload.task_id) ?? t.taskId;
    } else if (event.type === "remote.finished") {
      const t = start();
      t.finalState = text(payload.state);
      t.taskId = text(payload.task_id) ?? t.taskId;
      t.durationS = typeof payload.duration_s === "number" ? payload.duration_s : null;
      t.reason = text(payload.reason);
    } else if (event.type === "remote.card_changed") {
      const t = start();
      t.endpoint = text(payload.endpoint) ?? t.endpoint;
      t.cardChanged = { pinned: text(payload.pinned) ?? "", now: text(payload.now) ?? "" };
      t.reason = "remote_card_changed";
    }
  }
  return task;
}

/** What a person reads of a reason; the raw words stay in the journal. */
export function remoteReasonText(reason: string | null): string | null {
  switch (reason) {
    case null:
      return null;
    case "remote_card_changed":
      return "The agent's card changed since this version was published. Nothing was sent. Publish a new version to accept the change.";
    case "remote_auth_required":
      return "The remote agent refused the credential. Reconnect it in the administration.";
    case "remote_input_required":
      return "The remote agent asked a question and waits for an answer. Answering it is not supported yet.";
    case "remote_timeout":
      return "The remote agent did not finish in time; its task was cancelled.";
    case "remote_failed":
      return "The remote agent ended its task without success.";
    default:
      return reason.replace(/_/g, " ");
  }
}

/** The short form of a `sha256:` digest: enough to compare, short enough to read. */
export function shortDigest(digest: string | null | undefined): string {
  if (!digest) return "—";
  const hex = digest.replace(/^sha256:/, "");
  return `sha256:${hex.slice(0, 12)}…`;
}
