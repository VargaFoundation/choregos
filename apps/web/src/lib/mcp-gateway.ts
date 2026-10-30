// SPDX-License-Identifier: Apache-2.0
import type { McpGateway, OrgConnector } from "@/lib/types";

/** A server's name at the gateway: what `{server}` is replaced by. */
export const SERVER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$/;

export type GatewayMode = McpGateway["mode"];
export type GatewayRouting = McpGateway["routing"];
export type GatewayAuthType = McpGateway["auth"]["type"];

/** The three modes, each with the one line that says what it does (ADR 0105). */
export const MODES: { value: GatewayMode; label: string; note: string }[] = [
  { value: "off", label: "Off", note: "The gateway is kept but unused." },
  { value: "available", label: "Available", note: "A connector may go through the gateway; direct connectors still work." },
  {
    value: "required",
    label: "Required",
    note: "New direct MCP connectors are refused and existing ones are not called. A plugin's server is not concerned.",
  },
];

/** How Choregos authenticates to the gateway, and whether the card asks for a secret. */
export const AUTH_TYPES: { value: GatewayAuthType; label: string; needsSecret: boolean }[] = [
  { value: "bearer", label: "Bearer token", needsSecret: true },
  { value: "header", label: "Custom header", needsSecret: true },
  { value: "oauth_client_credentials", label: "OAuth client credentials", needsSecret: true },
  { value: "jwt_assertion", label: "Signed assertion (JWT)", needsSecret: false },
];

/** What is wrong with a gateway endpoint, or null: http(s), no credentials, query or fragment, `{server}` as the routing says. */
export function endpointProblem(endpoint: string, routing: GatewayRouting): string | null {
  let url: URL;
  try {
    url = new URL(endpoint.replace("{server}", "server"));
  } catch {
    return "Use an http or https URL, e.g. https://gateway.example.com/mcp/{server}.";
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return "Use an http or https URL.";
  if (/[@?#]/.test(endpoint)) return "No credentials, query or fragment in the endpoint.";
  const path = endpoint.replace(/^https?:\/\/[^/]*/, "");
  const inPath = path.split("{server}").length - 1;
  const total = endpoint.split("{server}").length - 1;
  if (routing === "path") {
    if (inPath !== 1 || total !== 1) return "With path routing, the path needs exactly one {server}.";
  } else if (total !== 0) {
    return "With header routing, the endpoint has no {server}.";
  }
  return null;
}

/**
 * A plugin's server is an organisation connector the platform runs (ADR 0096): `required` mode never
 * stops it. The explicit `server` field says so; the installer's `catalogue_entry` says it too.
 */
export function isPluginServer(c: OrgConnector): boolean {
  return Boolean(c.server) || (c.catalogue_entry ?? "").startsWith("plugin:");
}

/** The direct MCP connectors that `required` mode stops. */
export function directMcpConnectors(connectors: OrgConnector[]): OrgConnector[] {
  return connectors.filter((c) => c.type === "mcp" && !isPluginServer(c));
}
