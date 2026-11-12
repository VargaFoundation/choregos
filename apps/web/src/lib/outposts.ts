// SPDX-License-Identifier: Apache-2.0
/**
 * Outposts (ADR 0147): places a person enrols, of one organisation, that only dial out.
 *
 * Pure helpers here: what the operator of an outpost pastes (the policy file's lines that pin the
 * door's keys, ADR 0148 §13, and the enrolment command), where an outpost stands in words, and the
 * reading of the API's 409. The screens live in `components/outposts/`.
 */
import { ApiError } from "./api";
import { libelleDeCode, relative, toSentenceCase } from "./format";
import type { OutpostCallRecord, OutpostCallState, OutpostInstanceStatus, OutpostStatus } from "./types";

/** Where the daemon reads its policy unless told otherwise (`choregos-outpost --policy`). */
export const POLICY_FILE = "/etc/choregos-outpost/policy.yaml";

/** The `door_keys` line of an outpost's policy file: the only keys whose calls the outpost makes. */
export function doorKeysLine(keys: readonly string[]): string {
  return `door_keys: [${keys.map((key) => `"${key}"`).join(", ")}]`;
}

/**
 * The `control_plane` line of an outpost's policy file, or a comment where the deployment declares no
 * origin for its door: a line that looked like a value would be pasted as one.
 */
export function controlPlaneLine(origin: string | null | undefined): string {
  if (origin) return `control_plane: ${origin}`;
  return "# control_plane: this deployment declares no origin for the outposts' door; ask its operator";
}

/** The lines an operator puts in the policy file before the daemon starts. */
export function policyLines(origin: string | null | undefined, keys: readonly string[]): string {
  return `${controlPlaneLine(origin)}\n${doorKeysLine(keys)}`;
}

/** The command that enrols one instance, on the outpost's machine. */
export function enrolCommand(token: string): string {
  return `choregos-outpost enrol --token ${token}`;
}

/** Online, or when it was last seen: an outpost and an instance say it the same way. */
export function presence(subject: { online: boolean; last_seen_at?: string | null }): { online: boolean; label: string } {
  if (subject.online) return { online: true, label: "Online" };
  return { online: false, label: subject.last_seen_at ? `Last seen ${relative(subject.last_seen_at)}` : "Never seen" };
}

type BadgeTone = "ok" | "warn" | "danger" | "neutral" | "running";

export const OUTPOST_TONE: Record<OutpostStatus, BadgeTone> = { active: "ok", paused: "warn", revoked: "danger" };
/** An approved instance is `active` for the API; a person approved it, and the console says so. */
export const INSTANCE_LABEL: Record<OutpostInstanceStatus, string> = { pending: "Pending", active: "Approved", revoked: "Revoked" };
export const INSTANCE_TONE: Record<OutpostInstanceStatus, BadgeTone> = { pending: "warn", active: "ok", revoked: "neutral" };

/** The states of a call, in the order a call goes through them: the filter of the recent calls. */
export const CALL_STATES: readonly OutpostCallState[] = ["pending", "claimed", "answered", "refused", "expired", "timed_out", "in_doubt"];
export const CALL_TONE: Record<OutpostCallState, BadgeTone> = {
  pending: "neutral",
  claimed: "running",
  answered: "ok",
  refused: "danger",
  expired: "neutral",
  timed_out: "warn",
  in_doubt: "danger",
};

/** A state in words: `timed_out` reads "Timed out". */
export function stateLabel(state: string): string {
  return toSentenceCase(libelleDeCode(state));
}

/** What a call was, in a few words: the tool it called, or a listing of the endpoint's tools. */
export function callSubject(call: Pick<OutpostCallRecord, "kind" | "tool">): string {
  return call.kind === "list_tools" || !call.tool ? "Tool listing" : call.tool;
}

/** What the API said when it refused a gesture on an outpost: its code, its own words, what it names. */
export interface OutpostRefusal {
  status: number;
  code: string | null;
  /** The API's own sentence, shown as it is. */
  detail: string;
  /** `outpost_in_use`: the connectors that still route to the outpost. */
  connectors: string[];
}

export function refusalOf(cause: unknown): OutpostRefusal {
  if (cause instanceof ApiError) {
    return {
      status: cause.status,
      code: cause.problem.code ?? null,
      detail: cause.message,
      connectors: Array.isArray(cause.problem.connectors) ? cause.problem.connectors : [],
    };
  }
  return { status: 0, code: null, detail: cause instanceof Error ? cause.message : "refused", connectors: [] };
}

/**
 * The two refusals that are about the deployment, not about the outpost: which one it is, in a few
 * words above the API's own sentence. `null` for any other refusal.
 */
export function deploymentRefusal(refusal: OutpostRefusal): string | null {
  if (refusal.code === "outposts_disabled") return "Outposts are switched off on this deployment";
  if (refusal.code === "door_key_missing") return "The outposts' door has no signing key on this deployment";
  return null;
}
