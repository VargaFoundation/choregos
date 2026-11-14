// SPDX-License-Identifier: Apache-2.0
/**
 * Outposts (ADR 0147): places a person enrols, of one organisation, that only dial out.
 *
 * Pure helpers here: what the operator of an outpost pastes (the policy file's lines that pin the
 * door's keys, ADR 0148 §13, and the enrolment command), where an outpost stands in words, the reading
 * of the API's refusals (its 409, and for a connector at an outpost its 422 by field and its 429), what
 * the form of such a connector checks before the API does, and what an agent's access tab says of a tool
 * that lives at an outpost (ADR 0149 §10). The screens live in `components/outposts/`.
 */
import { ApiError } from "./api";
import { libelleDeCode, relative, toSentenceCase } from "./format";
import type {
  AgentAccessOutpost,
  ConnectorOperation,
  OperationPatch,
  OrgConnector,
  OutpostCallRecord,
  OutpostCallState,
  OutpostInstanceStatus,
  OutpostList,
  OutpostRefusalCode,
  OutpostStatus,
  PlatformArgument,
} from "./types";

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

/**
 * What the API said when it refused a gesture on an outpost, or on a connector reached through one: its
 * code, its own words, what it names.
 */
export interface OutpostRefusal {
  status: number;
  code: string | null;
  /** The API's own sentence, shown as it is. */
  detail: string;
  /** `outpost_in_use`: the connectors that still route to the outpost. */
  connectors: string[];
  /** The outpost and the alias a refusal of a connector at an outpost names (ADR 0149 §7). */
  outpost?: string;
  endpoint?: string;
  /** `outpost_refused`: the outpost's own code for why it did not make the call. */
  refusal?: OutpostRefusalCode;
  /** A 429: how many seconds before asking again. */
  retryAfter?: number;
  /** A 422 that names fields of a connector's configuration (`body.config.<field>`): each, with the API's words. */
  fields?: Record<string, string>;
}

/** The fields of a connector's configuration a 422 names (`loc: ["body", "config", field]`), with its words. */
function configFields(errors: unknown[] | undefined): Record<string, string> | undefined {
  const named: Record<string, string> = {};
  for (const error of errors ?? []) {
    const { loc, msg } = (error ?? {}) as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(loc) || loc[0] !== "body" || loc[1] !== "config" || typeof loc[2] !== "string") continue;
    named[loc[2]] = typeof msg === "string" ? msg : "refused";
  }
  return Object.keys(named).length > 0 ? named : undefined;
}

export function refusalOf(cause: unknown): OutpostRefusal {
  if (cause instanceof ApiError) {
    const problem = cause.problem;
    return {
      status: cause.status,
      code: problem.code ?? null,
      detail: cause.message,
      connectors: Array.isArray(problem.connectors) ? problem.connectors : [],
      outpost: problem.outpost,
      endpoint: problem.endpoint,
      refusal: problem.refusal,
      retryAfter: cause.status === 429 && typeof problem.retry_after === "number" ? problem.retry_after : undefined,
      fields: cause.status === 422 ? configFields(problem.errors) : undefined,
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

/**
 * What the deployment would refuse, said before anyone tries (S65-24): the list of outposts carries
 * whether outposts are switched on and whether the door has a signing key. Each is the sentence the
 * console shows in place of a gesture that would only be refused, or `null` when nothing stands in its
 * way. Unknown facts (the list not read yet) refuse nothing: the API still has the last word.
 */
export function deploymentReadiness(list: Pick<OutpostList, "enabled" | "door_signing_key"> | undefined): {
  create: string | null;
  enrol: string | null;
} {
  if (!list) return { create: null, enrol: null };
  const off = list.enabled
    ? null
    : "Outposts are switched off on this deployment (CHOREGOS_OUTPOSTS): nothing is created and no token issued until its operator turns them on. What exists can still be read, revoked and deleted.";
  const keyless = list.door_signing_key
    ? null
    : "The outposts' door has no signing key on this deployment: there is no key to pin, so no enrolment token is issued until its operator sets one.";
  return { create: off, enrol: off ?? keyless };
}

/**
 * The refusals of a gesture on an outpost's records that are not a mistake to correct but a rule to
 * know: which one, in a few words above the API's own sentence. `null` for any other refusal.
 */
export const GESTURE_REFUSAL: Record<string, string> = {
  approval_requires_session: "An instance is approved in a person's session",
  enrolment_used: "This token already enrolled an instance",
};

/**
 * The refusals a connector reached through an outpost meets where it is declared, edited or discovered
 * (ADR 0149 §7): what each says, in a few words, above the outpost's own.
 */
export const ROUTE_REFUSAL: Record<string, string> = {
  outpost_paused: "The outpost is handed nothing",
  outpost_offline: "The outpost is offline",
  outpost_refused: "The outpost refused the call",
  outpost_timeout: "The outpost did not answer in time",
};

/**
 * The title of a refusal that is about the route to an outpost: one of the four codes of its 409, or the
 * 429 of its quotas. `null` for any other refusal, which is shown as the API wrote it and nothing more.
 */
export function routeRefusal(refusal: OutpostRefusal): string | null {
  if (refusal.code && refusal.code in ROUTE_REFUSAL) return ROUTE_REFUSAL[refusal.code] ?? null;
  if (refusal.status === 429) return "Too many calls to the outpost";
  return null;
}

/** When to ask again after a 429, in words. */
export function retryIn(seconds: number): string {
  if (seconds < 60) return `Try again in ${seconds} second${seconds === 1 ? "" : "s"}.`;
  const minutes = Math.ceil(seconds / 60);
  return `Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`;
}

// ─── a connector at an outpost (ADR 0149) ───

/** The connector type whose tools live at an outpost. */
export const OUTPOST_CONNECTOR_TYPE = "mcp-outpost";
/** An endpoint alias, as the contract writes one (`ENDPOINT_ALIAS_PATTERN`): never an address. */
export const ENDPOINT_ALIAS = /^[a-z][a-z0-9-]{0,62}$/;

/** The bounds of a connector's `timeout_s`, read from its type's declared schema (the contract's 1 to 120, 30). */
export function timeoutBounds(schema: unknown): { min?: number; max?: number; fallback?: number } {
  const properties = (schema as { properties?: Record<string, { minimum?: unknown; maximum?: unknown; default?: unknown }> } | undefined)
    ?.properties;
  const timeout = properties?.timeout_s ?? {};
  const number = (value: unknown) => (typeof value === "number" ? value : undefined);
  return { min: number(timeout.minimum), max: number(timeout.maximum), fallback: number(timeout.default) };
}

/**
 * What the form says of a configuration before the API does, by field: an alias that is not one, a
 * timeout outside the type's bounds. A courtesy, so that "Declare" is not pressed for a bare 422: the API
 * stays the judge, and its 422 is shown on the field it names.
 */
export function outpostConfigProblems(config: Record<string, unknown>, schema: unknown): Record<string, string> {
  const problems: Record<string, string> = {};
  const endpoint = config.endpoint;
  if (typeof endpoint === "string" && endpoint !== "" && !ENDPOINT_ALIAS.test(endpoint)) {
    problems.endpoint = "An alias, never an address: a lowercase letter, then lowercase letters, digits or hyphens (63 at most).";
  }
  const timeout = config.timeout_s;
  const { min, max } = timeoutBounds(schema);
  if (timeout !== undefined && (typeof timeout !== "number" || Number.isNaN(timeout) || (min !== undefined && timeout < min) || (max !== undefined && timeout > max))) {
    problems.timeout_s = `A number of seconds${min !== undefined && max !== undefined ? ` between ${min} and ${max}` : ""}.`;
  }
  return problems;
}

// ─── how a write at an outpost is delivered (ADR 0149 §5-6) ───

/** The platform argument that is an idempotency key: listing it is the first statement. */
export const IDEMPOTENCY_KEY: PlatformArgument = "action_key";
/** The two fields of an operation's rule either of which opens a write through an outpost. */
export const DELIVERY_STATEMENTS = ["platform_arguments", "in_doubt_accepted"] as const;
export type DeliveryStatement = (typeof DELIVERY_STATEMENTS)[number];

/** What an administrator ticks: the operation takes the key, an in-doubt outcome is accepted. */
export interface DeliveryDraft {
  key: boolean;
  accepted: boolean;
}

/**
 * A write through an outpost: the only operation whose rule carries the two statements. The API refuses
 * `in_doubt_accepted` for anything else, and `platform_arguments` for a read (`outpost_writes.stated`).
 */
export function isOutpostWrite(connector: Pick<OrgConnector, "kind" | "type">, operation: Pick<ConnectorOperation, "access">): boolean {
  return connector.kind === "mcp" && connector.type === OUTPOST_CONNECTOR_TYPE && operation.access !== "read";
}

/** The two statements as the operation's rule holds them now. */
export function deliveryOf(operation: Pick<ConnectorOperation, "platform_arguments" | "in_doubt_accepted">): DeliveryDraft {
  return { key: (operation.platform_arguments ?? []).includes(IDEMPOTENCY_KEY), accepted: operation.in_doubt_accepted === true };
}

/**
 * Whether the tool's input schema has an `action_key` argument: the API refuses a key the server would
 * never read ("the platform cannot hand it one").
 */
export function hasKeyArgument(operation: Pick<ConnectorOperation, "input_schema">): boolean {
  const properties = (operation.input_schema as { properties?: unknown } | null | undefined)?.properties;
  return typeof properties === "object" && properties !== null && IDEMPOTENCY_KEY in properties;
}

/**
 * The part of an `OperationPatch` that carries what changed between the rule and the draft, and nothing
 * else: `platform_arguments` is the rule's list with `action_key` added or taken out (an empty list
 * clears it), `in_doubt_accepted` true or null (null withdraws it).
 */
export function deliveryPatch(
  operation: Pick<ConnectorOperation, "platform_arguments" | "in_doubt_accepted">,
  draft: DeliveryDraft,
): Pick<OperationPatch, DeliveryStatement> {
  const now = deliveryOf(operation);
  const patch: Pick<OperationPatch, DeliveryStatement> = {};
  if (draft.key !== now.key) {
    const others = (operation.platform_arguments ?? []).filter((name) => name !== IDEMPOTENCY_KEY);
    patch.platform_arguments = draft.key ? [...others, IDEMPOTENCY_KEY].sort() : others;
  }
  if (draft.accepted !== now.accepted) patch.in_doubt_accepted = draft.accepted ? true : null;
  return patch;
}

/** The statements a 422 names (`loc: ["body", "platform_arguments" | "in_doubt_accepted"]`), each with the API's words. */
export function statementRefusals(cause: unknown): Partial<Record<DeliveryStatement, string>> {
  if (!(cause instanceof ApiError) || cause.status !== 422) return {};
  const named: Partial<Record<DeliveryStatement, string>> = {};
  for (const error of cause.problem.errors ?? []) {
    const { loc, msg } = (error ?? {}) as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(loc) || loc[0] !== "body") continue;
    const field = DELIVERY_STATEMENTS.find((name) => name === loc[1]);
    if (field) named[field] = typeof msg === "string" ? msg : "refused";
  }
  return named;
}

/**
 * What a tool reached through an outpost says of the outpost now, in words: `Online` or `Offline` (the
 * presence the call's own route reads, which the API reads for it), and the outpost's status when it is
 * handed nothing (`Paused`, `Revoked`). The console only says it.
 */
export function reachability(outpost: AgentAccessOutpost): Array<{ label: string; tone: BadgeTone }> {
  const said: Array<{ label: string; tone: BadgeTone }> = [outpost.online ? { label: "Online", tone: "ok" } : { label: "Offline", tone: "warn" }];
  if (outpost.status && outpost.status !== "active") said.push({ label: stateLabel(outpost.status), tone: OUTPOST_TONE[outpost.status] });
  return said;
}
