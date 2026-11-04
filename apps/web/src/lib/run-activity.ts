// SPDX-License-Identifier: Apache-2.0
/**
 * A run's activity, readable (S25-02): what the agent called, in which state, in how long, and what
 * was refused to it, read back from the ACP journal above which the console shows it.
 *
 * A tool call is born from a `tool_call` and lives by its `tool_call_update`s, which carry only what
 * changes; all share a `toolCallId`. A permission request cites it (`toolCall.toolCallId`), so a
 * refusal shows on the call it blocks. A call counts once, whatever the number of its updates; the
 * stored journal and the live stream overlap, and an event number counts once.
 */
import type { RunEventDto } from "./types";

export type CallStatus = "pending" | "in_progress" | "completed" | "failed";

export type ToolCall = {
  id: string;
  title: string;
  /** The ACP kind: read, edit, delete, move, search, execute, think, fetch, switch_mode, other. */
  genre: string;
  status: CallStatus;
  /** The time of its first update, and the time it ended (`completed` or `failed`). */
  start: string;
  end?: string;
  durationMs?: number;
  /** The files it targets (`locations`). */
  paths: string[];
  /** The refusal that blocked it, if there is one. */
  refusals?: string;
  /** A sub-agent: the tool the agent launches to delegate a task. */
  sousAgent: boolean;
};

export type Refusal = { target: string; reason: string; call?: string };

export type RunActivity = { calls: ToolCall[]; refusals: Refusal[]; termines: number; sousAgents: number };

const STATUSES = new Set<CallStatus>(["pending", "in_progress", "completed", "failed"]);
const FINISHED = new Set<CallStatus>(["completed", "failed"]);

/**
 * The tool name of a sub-agent. ACP has no notion of it: a sub-agent is a tool call whose
 * programmatic name (`name`, or the one Claude Code slips into `_meta`) says the delegation.
 */
const SUB_AGENTS = /^(task|agent|subagent|dispatch_agent)$/i;

type Raw = Record<string, unknown>;

function toolName(update: Raw): string | undefined {
  if (typeof update.name === "string") return update.name;
  const meta = update._meta as { claudeCode?: { toolName?: unknown } } | undefined;
  return typeof meta?.claudeCode?.toolName === "string" ? meta.claudeCode.toolName : undefined;
}

/** A run's activity, read back from its journal. */
export function runActivity(events: RunEventDto[]): RunActivity {
  const bySeq = new Map<number, RunEventDto>();
  for (const event of events) bySeq.set(event.seq, event);
  const calls = new Map<string, ToolCall>();
  const refusals: Refusal[] = [];

  for (const event of [...bySeq.values()].sort((a, b) => a.seq - b.seq)) {
    const payload = (event.payload ?? {}) as Raw;
    if (event.type === "session/request_permission") {
      if (payload.allowed !== false) continue;
      const params = (payload.params ?? {}) as { toolCall?: { toolCallId?: unknown } };
      const id = typeof params.toolCall?.toolCallId === "string" ? params.toolCall.toolCallId : undefined;
      const reason = String(payload.reason ?? "refused");
      refusals.push({ target: String(payload.target ?? ""), reason: reason, call: id });
      continue;
    }
    if (event.type !== "session/update") continue;
    const update = (payload.update ?? payload) as Raw;
    const sessionUpdate = update.sessionUpdate;
    if ((sessionUpdate !== "tool_call" && sessionUpdate !== "tool_call_update") || typeof update.toolCallId !== "string") continue;
    const id = update.toolCallId;
    const call =
      calls.get(id) ??
      ({ id, title: id, genre: "other", status: "pending", start: event.ts, paths: [], sousAgent: false } as ToolCall);
    if (typeof update.title === "string" && update.title) call.title = update.title;
    if (typeof update.kind === "string") call.genre = update.kind;
    if (Array.isArray(update.locations)) {
      call.paths = (update.locations as { path?: unknown }[]).flatMap((l) => (typeof l?.path === "string" ? [l.path] : []));
    }
    const name = toolName(update);
    if (name && SUB_AGENTS.test(name)) call.sousAgent = true;
    const status = update.status as CallStatus;
    if (STATUSES.has(status)) {
      call.status = status;
      // The end is the FIRST update that finishes it: a repeat does not lengthen the call.
      if (FINISHED.has(status) && !call.end) {
        call.end = event.ts;
        call.durationMs = Math.max(0, Date.parse(event.ts) - Date.parse(call.start));
      }
    }
    calls.set(id, call);
  }

  // A refusal lands on the call it cites, after the fact: the request can precede the call in the journal.
  for (const r of refusals) {
    const call = r.call ? calls.get(r.call) : undefined;
    if (call && !call.refusals) call.refusals = r.reason;
  }
  const list = [...calls.values()];
  return {
    calls: list,
    refusals: refusals,
    termines: list.filter((a) => FINISHED.has(a.status)).length,
    sousAgents: list.filter((a) => a.sousAgent).length,
  };
}
