// SPDX-License-Identifier: Apache-2.0
/**
 * What a ticket waits for, in one sentence (S55-05). Its page showed a title, a badge and a journey map,
 * and left the reader to work out from them whether anything was expected of them. This says it first:
 * who is expected — you, a group, an agent, nobody —, by when, and the one gesture that moves it on.
 *
 * A pure function of the ticket and its journey: what the banner says is tested here, not in a page.
 */
import type { JourneyStep, WorkItemDto, WorkItemJourney } from "@/lib/types";

export type NextStepTone = "info" | "warn" | "danger" | "ok" | "neutral";

export type NextStepAction =
  /** A decision waits on this page: the banner leads to it. */
  | { kind: "decide"; label: string }
  /** The ticket stopped on an escalation: replaying the stage moves it on. */
  | { kind: "replay" }
  /** The ticket is paused. */
  | { kind: "resume" };

export interface NextStep {
  tone: NextStepTone;
  /** Who is expected, as a reader says it: "Your decision", "An agent is working". */
  title: string;
  /** What, and since or until when. */
  detail?: string;
  /** The deadline of a waiting decision, if any. */
  dueAt?: string | null;
  action?: NextStepAction;
}

const RUNNING = new Set(["queued", "running", "approved"]);

/** The step a person is expected on, with its group and its deadline. */
function waitingHuman(journey: WorkItemJourney | undefined): JourneyStep | undefined {
  return journey?.steps.find((s) => s.kind === "human" && !s.ended_at && s.status === "waiting");
}

/** The agent step running now, if any. */
function runningAgent(journey: WorkItemJourney | undefined): JourneyStep | undefined {
  return journey?.steps.find((s) => s.kind === "agent" && !s.ended_at && RUNNING.has(s.status));
}

const REQUEST_TITLE: Record<string, (subject: string) => string> = {
  approval: (subject) => `Approve or send back ${subject}`,
  question: (subject) => `Answer the question about ${subject}`,
  scope_change: (subject) => `Accept or refuse a change of scope for ${subject}`,
  task: (subject) => `Complete the task: ${subject}`,
};

const ACTION_LABEL: Record<string, string> = {
  approval: "Decide",
  question: "Answer",
  scope_change: "Decide",
  task: "Do the task",
};

export function nextStep(item: WorkItemDto, journey?: WorkItemJourney): NextStep {
  const dead =
    !!item.failure || (!!item.workflow_status && ["FAILED", "TERMINATED", "TIMED_OUT"].includes(item.workflow_status));
  if (dead) {
    return {
      tone: "danger",
      title: "Stopped by an error",
      detail: `It will not move again until it is restarted${item.failure?.activity ? ` (in ${item.failure.activity})` : ""}.`,
    };
  }
  if (item.closed_at || journey?.closed) {
    const declined = item.state_outcome === "declined" || item.state_outcome === "cancelled";
    return {
      tone: declined ? "neutral" : "ok",
      title: declined ? "Closed" : "Done",
      detail: `It ended in ${item.state_display ?? item.state}.`,
    };
  }
  if (item.pending_request) {
    const request = item.pending_request;
    const payload = request.payload ?? {};
    const subject = typeof payload.title === "string" && payload.title ? payload.title : "this ticket";
    const human = waitingHuman(journey);
    const who = human?.actor ? ` · expected from ${human.actor}` : "";
    return {
      tone: "warn",
      title: (REQUEST_TITLE[request.kind] ?? REQUEST_TITLE.approval!)(subject),
      detail: `A person is expected${who}.`,
      dueAt: request.due_at ?? human?.due_at ?? null,
      action: { kind: "decide", label: ACTION_LABEL[request.kind] ?? "Decide" },
    };
  }
  const last = journey?.moves.at(-1);
  if (journey && !journey.closed && last && ["escalate", "default"].includes(last.kind)) {
    return {
      tone: "danger",
      title: "Escalated: nobody is working on it",
      detail: last.reason ? `${last.reason}. Replay the stage, or take it over.` : "Replay the stage, or take it over.",
      action: { kind: "replay" },
    };
  }
  if (item.paused) {
    return { tone: "neutral", title: "Paused", detail: "Nothing moves until it is resumed.", action: { kind: "resume" } };
  }
  const agent = runningAgent(journey);
  const run = item.current_run;
  if (agent || run) {
    const role = agent?.role ?? agent?.actor ?? run?.stage_role ?? "an agent";
    const attempt = agent?.attempt ?? run?.attempt;
    return {
      tone: "info",
      title: "An agent is working on it",
      detail: `Nothing is expected from you for now (${role}${attempt && attempt > 1 ? `, attempt ${attempt}` : ""}).`,
    };
  }
  return {
    tone: "info",
    title: "Waiting for its next step",
    detail: `It is in ${item.state_display ?? item.state}; nothing is expected from you for now.`,
  };
}
