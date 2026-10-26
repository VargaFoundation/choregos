// SPDX-License-Identifier: Apache-2.0
import type { OutboundProxy } from "@/lib/types";

/** The contract's pattern for `endpoint` (ADR 0087): `host:port`, no scheme, no credentials. */
export const ENDPOINT_PATTERN = /^[A-Za-z0-9.-]{1,253}:[0-9]{1,5}$/;

export type ProxyKind = OutboundProxy["applies_to"][number];

/** The traffic an outbound proxy can carry; `search_engines` has no effect until the SearXNG relay (S40-26d, ADR 0087 section 7); the console says "no effect yet", never a story id. */
export const PROXY_KINDS: { value: ProxyKind; label: string; note?: string }[] = [
  { value: "web_fetch", label: "Web reading (web_fetch)" },
  { value: "web_search", label: "Web search (web_search)" },
  { value: "knowledge_web", label: "Knowledge-base web crawl (knowledge_web)" },
  { value: "search_engines", label: "Search engines (search_engines)", note: "no effect yet" },
];

/** What is wrong with an endpoint, or null: the pattern, and a port that is a port. */
export function endpointProblem(endpoint: string): string | null {
  if (!ENDPOINT_PATTERN.test(endpoint)) return "Use host:port, without a scheme or credentials, e.g. gate.example.com:7000.";
  const port = Number(endpoint.slice(endpoint.lastIndexOf(":") + 1));
  return port >= 1 && port <= 65535 ? null : "The port is between 1 and 65535.";
}
