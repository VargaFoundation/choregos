// SPDX-License-Identifier: Apache-2.0
import type { ConnectorDiscovery } from "@/lib/types";

/** What the discovery changed, in one sentence. */
export function discoverySummary(diff: ConnectorDiscovery): string {
  const morceaux = [
    diff.added?.length ? `${diff.added.length} new (closed until you open them)` : "",
    diff.changed?.length ? `${diff.changed.length} changed their schema (closed again)` : "",
    diff.removed?.length ? `${diff.removed.length} removed` : "",
  ].filter(Boolean);
  return morceaux.length ? morceaux.join(", ") : `nothing changed (${diff.unchanged ?? 0} tools)`;
}
