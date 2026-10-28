// SPDX-License-Identifier: Apache-2.0
/** The MCP catalogue as the console reads it (ADR 0065): filters and the words that explain an entry. */
import { matchesText, toggled, type CatalogueFilters } from "@/lib/catalogue";
import type { McpCatalogueEntry } from "@/lib/types";

export function filterMcpEntries(entries: McpCatalogueEntry[], filters: CatalogueFilters): McpCatalogueEntry[] {
  return entries.filter(
    (e) =>
      matchesText([e.id, e.title, e.publisher, e.description].join(" "), filters.q) &&
      (!filters.category || e.categories.includes(filters.category)) &&
      (!toggled(filters, "official") || e.official) &&
      (!toggled(filters, "installable") || e.install === "installable"),
  );
}

/** Why an entry cannot be installed from here; `null` when it can. */
export function whyNotInstallable(entry: McpCatalogueEntry): string | null {
  if (entry.install === "needs_oauth") {
    return "Needs OAuth: each user authorises it, which an organisation connector cannot carry yet.";
  }
  if (entry.install === "needs_hosting") {
    return "Runs as a local process: hosting a third party's code is your operator's decision, not an install button.";
  }
  return null;
}

/** How the entry authenticates, in words. */
export function authLabel(auth: string): string {
  if (auth === "none") return "No credential";
  if (auth === "bearer") return "Bearer token";
  if (auth === "oauth") return "OAuth";
  if (auth.startsWith("header:")) return `Key in ${auth.slice("header:".length)}`;
  return auth;
}

/** The host of an entry's remote, the one the API must be able to reach. */
export function hostOf(entry: McpCatalogueEntry): string | null {
  return entry.egress_hosts?.[0] ?? null;
}
