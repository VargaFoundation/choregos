// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { InstallDrawer } from "@/components/catalogue/install-drawer";
import { ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import type { ConnectorEnablement } from "@/lib/types";

/**
 * Turns an installed organisation connector on or off per project (ADR 0074). It lists the projects
 * the server says the caller may act in (`can.enable_projects`); each switch calls the API and the
 * catalogues are read again. A refusal shows the API's own message.
 */
export function EnablementDrawer({
  org,
  enablement,
  projects,
  onClose,
}: {
  org: string;
  enablement: ConnectorEnablement;
  projects: string[];
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [on, setOn] = useState<Set<string>>(new Set(enablement.enabled_projects));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  function mark(slug: string, enabled: boolean) {
    setOn((current) => {
      const next = new Set(current);
      if (enabled) next.add(slug);
      else next.delete(slug);
      return next;
    });
  }
  async function toggle(slug: string, enabled: boolean) {
    setError(null);
    setBusy(slug);
    mark(slug, enabled);
    try {
      if (enabled) await api.enableOrgConnector(slug, enablement.connector);
      else await api.disableOrgConnector(slug, enablement.connector);
      await client.invalidateQueries({ queryKey: ["mcp-catalogue", org] });
      await client.invalidateQueries({ queryKey: ["connector-catalogue", org] });
    } catch (cause) {
      mark(slug, !enabled);
      setError(cause instanceof Error ? cause.message : "refused");
    } finally {
      setBusy(null);
    }
  }
  return (
    <InstallDrawer title={enablement.connector} onClose={onClose} verb="Projects for">
      <div className="space-y-3 text-sm">
        <p className="text-ink-muted">
          {enablement.availability === "selected_projects"
            ? "Only the projects switched on below reach this connector."
            : "This connector is on for every project: its administrator restricts it first."}
        </p>
        {projects.length === 0 ? (
          <p className="text-ink-muted">You can switch it on for no project.</p>
        ) : (
          <ul className="space-y-2">
            {projects.map((slug) => (
              <li key={slug}>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={on.has(slug)}
                    disabled={busy === slug || enablement.availability !== "selected_projects"}
                    onChange={(event) => void toggle(slug, event.target.checked)}
                  />
                  <span data-donnee>{slug}</span>
                </label>
              </li>
            ))}
          </ul>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </InstallDrawer>
  );
}
