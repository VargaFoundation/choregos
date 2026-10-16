// SPDX-License-Identifier: Apache-2.0
/** The connector catalogue as the console reads it (ADR 0074): the filters of the page. */
import { matchesText, toggled, type CatalogueFilters } from "@/lib/catalogue";
import type { ConnectorCatalogueEntry } from "@/lib/types";

/** The entries a filter lets through. Both scope toggles on, or none on, mean every scope. */
export function filterConnectorEntries(entries: ConnectorCatalogueEntry[], filters: CatalogueFilters): ConnectorCatalogueEntry[] {
  const org = toggled(filters, "organisation");
  const project = toggled(filters, "project");
  return entries.filter(
    (entry) =>
      (!filters.category || entry.family === filters.category) &&
      (org === project || (org ? entry.scope === "organisation" : entry.scope === "project")) &&
      matchesText([entry.display, entry.kind, entry.type, entry.family, ...entry.capabilities].join(" "), filters.q),
  );
}
