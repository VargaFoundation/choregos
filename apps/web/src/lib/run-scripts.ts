// SPDX-License-Identifier: Apache-2.0
/**
 * The scripts a run's agent wrote to call its tools (ADR 0122 §4, S57-10), read back from its journal.
 *
 * The runner's tool bridge journals three things: whether the run's client was written
 * (`tools.client`), each script's text once (`tools.script`: `sha256`, `bytes`, `text`), and every call
 * it relayed (`tools.script.call`: `tool`, `status_code`, `duration_ms`, `response_bytes`,
 * `script_sha256`). A call names its script by digest; a script piped on standard input has no file,
 * so its calls carry no digest and its text is never sent. Calls the model makes itself are not here:
 * they are the ACP journal's tool calls (`run-activity.ts`).
 *
 * A proposal comes back as `202`. The bridge's journal line does not say which action it created
 * (the access record does not either): `action` is read when a line carries it, and is null otherwise.
 */
import type { RunEventDto } from "./types";

export type ScriptCall = {
  seq: number;
  ts: string;
  tool: string;
  /** The answer's own status on a `200`, `202` for a proposal, the HTTP status of a problem. */
  statusCode: number | null;
  durationMs: number | null;
  responseBytes: number | null;
  /** The governed action a `202` named, when the journal line records it. */
  action: string | null;
};

export type RunScript = {
  /** `null`: calls that carried no digest — scripts piped on standard input, which cannot be told apart. */
  sha256: string | null;
  /** `null`: the text never reached the bridge (piped on standard input, or its post failed). */
  text: string | null;
  bytes: number | null;
  calls: ScriptCall[];
};

export type ScriptClient = { written: boolean; tools: number | null; modules: string[]; error: string | null };

export type RunScripts = { client: ScriptClient | null; scripts: RunScript[] };

/** What a call's outcome reads as. `proposed` is never a success: nothing was done when the script got it. */
export type CallOutcome = "done" | "proposed" | "cap" | "withheld" | "timeout" | "unreachable" | "failed" | "unknown";

const SHA256 = /^[0-9a-f]{64}$/;
/** An action id the console will put in a link: what the API mints, nothing that could leave the page. */
const ACTION_ID = /^[A-Za-z0-9_-]{1,64}$/;

type Payload = Record<string, unknown>;

const number = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);
const digest = (value: unknown): string | null => (typeof value === "string" && SHA256.test(value) ? value : null);

export function outcomeOf(statusCode: number | null): CallOutcome {
  if (statusCode === null) return "unknown";
  if (statusCode === 202) return "proposed";
  if (statusCode === 429) return "cap";
  if (statusCode === 451) return "withheld";
  if (statusCode === 504) return "timeout";
  if (statusCode === 502) return "unreachable";
  if (statusCode >= 200 && statusCode < 300) return "done";
  return "failed";
}

/** The first characters of a digest, enough to tell two scripts of one run apart. */
export const shortDigest = (sha256: string): string => sha256.slice(0, 12);

/** `null` when the run was offered no tool scripts and ran none: nothing to show. */
export function runScriptsOf(events: RunEventDto[]): RunScripts | null {
  const bySeq = new Map<number, RunEventDto>();
  for (const event of events) bySeq.set(event.seq, event);
  const ordered = [...bySeq.values()].sort((a, b) => a.seq - b.seq);

  let client: ScriptClient | null = null;
  // In the order a script first appears: its text, or its first call.
  const scripts = new Map<string, RunScript>();
  const scriptOf = (sha256: string | null): RunScript => {
    const key = sha256 ?? "";
    const found = scripts.get(key);
    if (found) return found;
    const created: RunScript = { sha256, text: null, bytes: null, calls: [] };
    scripts.set(key, created);
    return created;
  };

  for (const event of ordered) {
    const payload = (event.payload ?? {}) as Payload;
    if (event.type === "tools.client") {
      client = {
        written: payload.written === true,
        tools: number(payload.tools),
        modules: Array.isArray(payload.modules) ? payload.modules.filter((m): m is string => typeof m === "string") : [],
        error: typeof payload.error === "string" && payload.error ? payload.error : null,
      };
    } else if (event.type === "tools.script") {
      const sha256 = digest(payload.sha256);
      if (sha256 === null || typeof payload.text !== "string") continue;
      const script = scriptOf(sha256);
      script.text = payload.text;
      script.bytes = number(payload.bytes);
    } else if (event.type === "tools.script.call") {
      const action = typeof payload.action === "string" && ACTION_ID.test(payload.action) ? payload.action : null;
      scriptOf(digest(payload.script_sha256)).calls.push({
        seq: event.seq,
        ts: event.ts,
        tool: typeof payload.tool === "string" && payload.tool ? payload.tool : "—",
        statusCode: number(payload.status_code),
        durationMs: number(payload.duration_ms),
        responseBytes: number(payload.response_bytes),
        action,
      });
    }
  }

  if (client === null && scripts.size === 0) return null;
  return { client, scripts: [...scripts.values()] };
}
