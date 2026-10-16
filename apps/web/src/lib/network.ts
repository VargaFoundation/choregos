// SPDX-License-Identifier: Apache-2.0
/**
 * Network access (ADR 0070), as the console reads it: the host-pattern grammar (a mirror of
 * `choregos_core.network.validate_pattern`, so a typo is flagged before the request; the server stays
 * the judge), the organisation's ceiling section, and the sentences that say what an agent may reach.
 */
import type { AgentNetwork } from "./types";

/** The organisation's `network` ceiling section (the ceiling itself is an open object in the contract). */
export type CeilingNetwork = {
  /** `null`: no cap; `["*"]`: any public host; a list: only these (and their subdomains). */
  allow_domains?: string[] | null;
  deny_domains?: string[];
  per_host_per_minute?: number | null;
  per_run_per_minute?: number | null;
  builtin_web_tools?: "allow" | "deny" | null;
  user_agent_contact?: string | null;
};

const LABEL = /^[a-z0-9_-]+$/;

/** The normalised pattern, or why it is refused. */
export function validatePattern(raw: string): { ok: true; value: string } | { ok: false; reason: string } {
  const value = raw.trim().toLowerCase().replace(/\.$/, "");
  if (!value) return { ok: false, reason: "a host pattern cannot be empty" };
  if (value === "*") return { ok: true, value };
  for (const [bad, why] of [["://", "a scheme"], ["/", "a path"], ["@", "user-info"], [":", "a port"], [" ", "a space"]] as const) {
    if (value.includes(bad)) return { ok: false, reason: `\`${raw}\` is not a host pattern: it carries ${why}` };
  }
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(value) || value.includes("[")) return { ok: false, reason: `\`${raw}\` is an IP address: write the host name` };
  const body = value.startsWith("*.") ? value.slice(2) : value;
  if (body.includes("*")) return { ok: false, reason: `\`${raw}\`: \`*\` is only allowed alone or as a leading \`*.\`` };
  const labels = body.split(".");
  if (value.startsWith("*.") && labels.length < 2) return { ok: false, reason: `\`${raw}\`: \`*.\` needs a domain with at least two labels after it` };
  for (const label of labels) {
    if (!label) return { ok: false, reason: `\`${raw}\` has an empty label` };
    if (!LABEL.test(label)) return { ok: false, reason: `\`${raw}\` has a character a host name cannot carry` };
  }
  return { ok: true, value };
}

/** A list typed one host per line (commas and spaces split too): the valid patterns, and a line per refusal. */
export function parseHosts(text: string): { hosts: string[]; problems: string[] } {
  const hosts: string[] = [];
  const problems: string[] = [];
  for (const part of text.split(/[\s,]+/).filter(Boolean)) {
    const checked = validatePattern(part);
    if (!checked.ok) problems.push(checked.reason);
    else if (!hosts.includes(checked.value)) hosts.push(checked.value);
  }
  return { hosts, problems };
}

export function hostMatches(host: string, pattern: string): boolean {
  const name = host.trim().toLowerCase();
  if (pattern === "*") return true;
  if (pattern.startsWith("*.")) return name.endsWith(pattern.slice(1));
  return name === pattern || name.endsWith(`.${pattern}`);
}

/** Whether every host `inner` matches is also matched by `outer`. */
export function patternWithin(inner: string, outer: string): boolean {
  if (outer === "*") return true;
  if (inner === "*") return false;
  const apex = inner.startsWith("*.") ? inner.slice(2) : inner;
  if (outer.startsWith("*.")) {
    const base = outer.slice(2);
    return inner.startsWith("*.") ? apex === base || apex.endsWith(`.${base}`) : apex.endsWith(`.${base}`);
  }
  return apex === outer || apex.endsWith(`.${outer}`);
}

/** The hosts of an agent the organisation's ceiling refuses, each with the reason (the API says it again at publication). */
export function flaggedByCeiling(domains: string[], ceiling: CeilingNetwork | null | undefined): Array<{ host: string; reason: string }> {
  if (!ceiling) return [];
  const out: Array<{ host: string; reason: string }> = [];
  for (const host of domains) {
    const checked = validatePattern(host);
    if (!checked.ok) continue;
    const denied = (ceiling.deny_domains ?? []).find((d) => patternWithin(checked.value, d) || hostMatches(checked.value.replace(/^\*\./, ""), d));
    if (denied) out.push({ host, reason: `denied by the organisation (${denied})` });
    else if (ceiling.allow_domains != null && !ceiling.allow_domains.some((a) => patternWithin(checked.value, a)))
      out.push({ host, reason: "outside the organisation's allowed hosts" });
  }
  return out;
}

/** The three ways an agent version may name its hosts. */
export type NetworkMode = "project" | "none" | "listed";

export function networkMode(network: AgentNetwork | undefined): NetworkMode {
  if (network?.domains == null) return "project";
  return network.domains.length === 0 ? "none" : "listed";
}

/** Whether an agent's network is the default (nothing written): then the API drops it from the digest. */
export function isDefaultNetwork(network: AgentNetwork | undefined): boolean {
  return network == null || (network.domains == null && network.per_host_per_minute == null && network.builtin_web_tools == null);
}

/** One line: what the agent may reach. */
export function networkSummary(network: AgentNetwork | undefined): string {
  const mode = networkMode(network);
  const hosts = mode === "project" ? "the project's hosts" : mode === "none" ? "no internet" : `${network!.domains!.length} host${network!.domains!.length === 1 ? "" : "s"}`;
  const extra = [
    network?.per_host_per_minute != null ? `${network.per_host_per_minute}/min per host` : null,
    network?.builtin_web_tools === false ? "built-in web tools off" : null,
  ].filter((part): part is string => part !== null);
  return [hosts, ...extra].join(" · ");
}
