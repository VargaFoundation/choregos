// SPDX-License-Identifier: Apache-2.0
/**
 * Network access (S39-22, ADR 0066): what the console says about the hosts a governed "add" needs.
 *
 * The API answers a 409 `egress_required` when an add needs a host its organisation has not opened.
 * Pure helpers here (the sentences, the family names, the reading of that 409); the modal and the
 * `withEgress` gesture live in `components/egress/`.
 */
import { ApiError } from "./api";
import type { EgressOverview, EgressRule, EgressVerdict } from "./types";

export type Family = EgressVerdict["used_by"];

/** The pod family that makes the call, in the words a person uses. */
export const FAMILY_LABEL: Record<Family, string> = {
  api: "the API",
  workers: "the workers",
  runners: "agent pods",
};

/** The verdicts a 409 `egress_required` carries, or `null` for any other error. */
export function egressRequired(error: unknown): EgressVerdict[] | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const { code, verdicts } = error.problem;
  return code === "egress_required" && Array.isArray(verdicts) && verdicts.length > 0 ? verdicts : null;
}

export function hostLabel(verdict: Pick<EgressVerdict, "host" | "port">): string {
  return verdict.port === 443 ? verdict.host : `${verdict.host}:${verdict.port}`;
}

/** "This organisation": `allowed`, `requested by X on D`, or `not allowed`. */
export function orgLine(verdict: EgressVerdict): string {
  if (verdict.org === "allowed") return "allowed";
  if (verdict.org === "requested") {
    const by = verdict.requested_by ? ` by ${verdict.requested_by}` : "";
    const on = verdict.requested_at ? ` on ${new Date(verdict.requested_at).toISOString().slice(0, 10)}` : "";
    return `requested${by}${on}, waiting for an administrator`;
  }
  return "not allowed";
}

/** "The platform's network", as this deployment declares it. */
export function ceilingLine(verdict: EgressVerdict): string {
  if (verdict.ceiling === "open") return "declared open";
  if (verdict.ceiling === "outside") return "outside the declared ceiling";
  return "not declared by this deployment";
}

/** Whether a host needs a change the platform team makes (the app cannot). */
export function needsPlatformChange(verdicts: EgressVerdict[]): boolean {
  return verdicts.some((v) => v.platform_change.needed);
}

/** The approvers of the first verdict that lists some: the same people decide every host. */
export function approversOf(verdicts: EgressVerdict[]): { display_name: string; email: string }[] {
  return verdicts.find((v) => (v.approvers ?? []).length > 0)?.approvers ?? [];
}

/** Thrown by the guard when the person closes the modal without choosing: the add did not happen. */
export class EgressCancelled extends Error {
  constructor() {
    super("cancelled: the hosts were not opened");
    this.name = "EgressCancelled";
  }
}

/** Thrown after a request to the administrators was filed: the add waits for their decision. */
export class EgressRequested extends Error {
  constructor(readonly hosts: string[]) {
    super(`requested: an administrator must open ${hosts.join(", ")} first — retry once it is approved`);
    this.name = "EgressRequested";
  }
}

/** `*.x.y` covers `a.x.y`, never `x.y`; anything else is exact (the server's own rule). */
export function hostMatches(pattern: string, host: string): boolean {
  if (pattern.startsWith("*.")) return host.endsWith(pattern.slice(1)) && host.length > pattern.length - 1;
  return pattern === host;
}

/**
 * The allowed hosts the deployment's ceiling does not cover, per family: what the platform must
 * still open for them to work. A family without a declared ceiling is unknown, so never listed.
 */
export function outsideCeiling(overview: EgressOverview): { rule: EgressRule; family: Family }[] {
  const out: { rule: EgressRule; family: Family }[] = [];
  for (const rule of overview.rules) {
    if (rule.status !== "allowed") continue;
    for (const family of rule.used_by) {
      const patterns = overview.ceiling[family] ?? [];
      if (patterns.length > 0 && !patterns.some((p) => hostMatches(p, rule.host))) out.push({ rule, family });
    }
  }
  return out;
}
