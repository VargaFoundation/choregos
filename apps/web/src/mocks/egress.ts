// SPDX-License-Identifier: Apache-2.0
/**
 * The demo's network access (S39-22): one allowed host, one request waiting, a ceiling that leaves
 * the MCP hosts outside — so the modal shows its platform change. The governed "add" endpoints answer
 * 409 `egress_required` until their host is allowed, as the API does.
 *
 * Who is looking: an administrator unless `sessionStorage["choregos.mock.role"]` is `developer`.
 */
import { ApiError } from "@/lib/api";
import type { EgressRule, EgressVerdict } from "@/lib/types";
import mcpCatalogueFile from "./mcp-catalogue.json";

const CEILING: Record<string, string[]> = {
  api: ["api.github.com", "github.com", "codeload.github.com"],
  workers: ["api.github.com", "*.atlassian.net"],
};

const iso = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

export const egressRules: EgressRule[] = [
  { id: "eg-1", host: "api.github.com", ports: [443], used_by: ["api"], status: "allowed", reason: "seeded from the skill sources", origin: "migration", requested_by: "platform", requested_at: iso(60 * 24), decided_by: "platform", decided_at: iso(60 * 24) },
  { id: "eg-2", host: "codeload.github.com", ports: [443], used_by: ["api"], status: "allowed", reason: "seeded from the skill sources", origin: "migration", requested_by: "platform", requested_at: iso(60 * 24), decided_by: "platform", decided_at: iso(60 * 24) },
  { id: "eg-3", host: "mcp.pending.test", ports: [443], used_by: ["api"], status: "requested", reason: "the vendor connector", origin: "org-connector:vendor", requested_by: "lea@acme.dev", requested_at: iso(90) },
];

let counter = 3;

function role(): "org_admin" | "developer" {
  try {
    return typeof window !== "undefined" && window.sessionStorage.getItem("choregos.mock.role") === "developer" ? "developer" : "org_admin";
  } catch {
    return "org_admin";
  }
}

function hostMatches(pattern: string, host: string): boolean {
  return pattern.startsWith("*.") ? host.endsWith(pattern.slice(1)) && host.length > pattern.length - 1 : pattern === host;
}

function verdict(host: string, port: number, family: "api" | "workers" | "runners", reason: string): EgressVerdict {
  const rule = egressRules.find((r) => ["allowed", "requested"].includes(r.status) && r.used_by.includes(family) && hostMatches(r.host, host));
  const patterns = CEILING[family] ?? [];
  const ceiling = patterns.length === 0 ? "unknown" : patterns.some((p) => hostMatches(p, host)) ? "open" : "outside";
  const needed = ceiling !== "open";
  const snippet = `# Allow egress from the ${family} pods to:\ntoFQDNs:\n  - matchName: "${host}"\ntoPorts: [${port}]\n# Reason: ${reason}\n`;
  return {
    host,
    port,
    used_by: family,
    org: rule ? (rule.status === "allowed" ? "allowed" : "requested") : "absent",
    ceiling,
    can_allow: role() === "org_admin",
    approvers: [{ display_name: "Augustin", email: "augustin@acme.dev" }],
    platform_change: needed
      ? {
          needed: true,
          format: "text",
          snippet,
          owner: "the platform team",
          issue_url: `https://git.example.test/platform/issues/new?title=${encodeURIComponent(`egress: ${host}`)}&body=${encodeURIComponent(snippet)}`,
        }
      : { needed: false },
    requested_by: rule?.status === "requested" ? rule.requested_by : null,
    requested_at: rule?.status === "requested" ? rule.requested_at : null,
  };
}

function urlHost(url: unknown): string | null {
  try {
    return typeof url === "string" ? new URL(url).hostname : null;
  } catch {
    return null;
  }
}

/** The hosts a governed "add" needs, or `null` when the route is not one. */
function needed(method: string, route: string, body: Record<string, unknown>): { origin: string; reason: string; hosts: string[] } | null {
  if (method === "POST" && /^\/orgs\/[^/]+\/connectors$/.test(route) && body.type === "mcp") {
    const host = urlHost((body.config as Record<string, unknown> | undefined)?.url);
    return host ? { origin: `org-connector:${String(body.name)}`, reason: `the mcp connector ${String(body.name)}`, hosts: [host] } : null;
  }
  const install = /^\/orgs\/[^/]+\/mcp-catalogue\/([^/]+)\/install$/.exec(route);
  if (method === "POST" && install) {
    const entry = (mcpCatalogueFile.entries as { id: string; egress_hosts?: string[] }[]).find((e) => e.id === install[1]);
    const hosts = entry?.egress_hosts ?? [];
    return hosts.length > 0 ? { origin: `mcp-catalogue:${install[1]}`, reason: `the ${install[1]} MCP server`, hosts } : null;
  }
  if (method === "POST" && /^\/orgs\/[^/]+\/skills\/import-git(\/preview)?$/.test(route)) {
    const repo = String(body.repo_url ?? "");
    const host = urlHost(repo);
    if (!host) return null;
    const hosts = host === "github.com" ? ["api.github.com", "codeload.github.com"] : [host];
    return { origin: `skill-import:${repo.replace(/^https:\/\//, "")}`, reason: `importing skills from ${repo}`, hosts };
  }
  if (method === "POST" && /^\/orgs\/[^/]+\/knowledge\/[^/]+\/sources$/.test(route) && ["web", "git"].includes(String(body.kind))) {
    const host = urlHost((body.config as Record<string, unknown> | undefined)?.url);
    return host ? { origin: `knowledge-source:${String(body.name)}`, reason: `the ${String(body.kind)} knowledge source`, hosts: [host] } : null;
  }
  return null;
}

/** `undefined` lets the rest of the mock answer. */
export function egressApi(method: string, route: string, body: unknown): unknown {
  const payload = (body ?? {}) as Record<string, unknown>;
  const gate = needed(method, route, payload);
  if (gate) {
    const missing = gate.hosts.filter((h) => verdict(h, 443, "api", gate.reason).org !== "allowed");
    if (missing.length > 0) {
      const verdicts = missing.map((h) => verdict(h, 443, "api", gate.reason));
      if (!payload.allow_egress) {
        throw new ApiError(409, { title: "Network access required", detail: `acme has not opened ${missing.join(", ")}`, code: "egress_required", verdicts });
      }
      if (role() !== "org_admin") {
        throw new ApiError(403, { title: "Forbidden", detail: "opening these hosts is decided by the administrators (augustin@acme.dev): ask them" });
      }
      for (const host of missing) {
        egressRules.push({ id: `eg-${++counter}`, host, ports: [443], used_by: ["api"], status: "allowed", reason: gate.reason, origin: gate.origin, requested_by: "augustin@acme.dev", requested_at: iso(0), decided_by: "augustin@acme.dev", decided_at: iso(0) });
      }
    }
    return undefined;
  }
  if (!route.startsWith("/orgs/") || !route.includes("/egress")) return undefined;
  const rest = route.replace(/^\/orgs\/[^/]+\/egress/, "");
  if (method === "GET" && rest === "") {
    return {
      mode: "enforce",
      ceiling: CEILING,
      rules: egressRules.filter((r) => r.status !== "requested"),
      requests: egressRules.filter((r) => r.status === "requested"),
      can_decide: role() === "org_admin",
    };
  }
  if (method === "POST" && rest === "/check") {
    const targets = (payload.targets as { host?: string; port?: number; used_by?: ("api" | "workers" | "runners")[] }[] | undefined) ?? [];
    const verdicts = targets.flatMap((t) => (t.used_by ?? ["api"]).map((f) => verdict(String(t.host), t.port ?? 443, f, String(payload.reason ?? ""))));
    return { verdicts, ok: verdicts.every((v) => v.org === "allowed" && v.ceiling !== "outside") };
  }
  if (method === "POST" && rest === "/requests") {
    const rule: EgressRule = { id: `eg-${++counter}`, host: String(payload.host), ports: (payload.ports as number[]) ?? [443], used_by: (payload.used_by as EgressRule["used_by"]) ?? ["api"], status: "requested", reason: String(payload.reason ?? ""), origin: String(payload.origin ?? "manual"), requested_by: "lea@acme.dev", requested_at: iso(0) };
    egressRules.push(rule);
    return rule;
  }
  const decision = /^\/requests\/([^/]+)\/(approve|refuse)$/.exec(rest);
  if (method === "POST" && decision) {
    const rule = egressRules.find((r) => r.id === decision[1]);
    if (!rule) throw new ApiError(404, { title: "Egress rule not found" });
    rule.status = decision[2] === "approve" ? "allowed" : "refused";
    rule.decided_by = "augustin@acme.dev";
    rule.decided_at = iso(0);
    return rule;
  }
  const revoke = /^\/rules\/([^/]+)$/.exec(rest);
  if (method === "DELETE" && revoke) {
    const rule = egressRules.find((r) => r.id === revoke[1]);
    if (!rule) throw new ApiError(404, { title: "Egress rule not found" });
    rule.status = "revoked";
    return rule;
  }
  return undefined;
}
