// SPDX-License-Identifier: Apache-2.0
import type { ConnectorDto } from "@/lib/types";

/** Where a project's work items live: in Choregos itself, or in an outside tracker. */
export interface TrackerInfo {
  external: boolean;
  type: string;
  /** The repository or project the issues live in, when the connector says it. */
  where?: string;
}

const INTERNAL = new Set(["internal", "fake"]);

/**
 * The project's tracker, read from its connectors. No tracker row means internal, as for the API
 * (a fresh project files its work items in Choregos): the board must not hide its button then.
 */
export function trackerOf(connectors: readonly ConnectorDto[] | undefined): TrackerInfo {
  const row = (connectors ?? []).find((c) => c.kind === "tracker");
  if (!row || INTERNAL.has(row.type)) return { external: false, type: row?.type ?? "internal" };
  const config = (row.config ?? {}) as Record<string, unknown>;
  const where = ["repo", "project_key", "project"].map((key) => config[key]).find((v) => typeof v === "string" && v);
  return { external: true, type: row.type, where: where as string | undefined };
}
