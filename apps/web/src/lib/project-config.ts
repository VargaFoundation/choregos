// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import type { ProjectDto } from "@/lib/types";

/** The project's configuration as the API validates it (`ProjectConfig`); `dod` is not in the generated type yet. */
export type ProjectConfigValue = ProjectDto["config"] & { dod?: { facts?: Record<string, string> } };

/** A section's change: the keys it owns, or a function of the latest configuration returning them. */
export type ConfigChange = Partial<ProjectConfigValue> | ((latest: ProjectConfigValue) => Partial<ProjectConfigValue>);

/**
 * The latest configuration with a section's change applied. Every key the change does not name is kept
 * as it is, so a section never drops what another one wrote; a key set to `undefined` is removed.
 * `PATCH /projects/{id}` replaces the whole configuration, which is why the client sends all of it.
 */
export function mergeConfig(latest: ProjectConfigValue, change: ConfigChange): ProjectConfigValue {
  const own = typeof change === "function" ? change(latest) : change;
  const merged: Record<string, unknown> = { ...latest, ...own };
  for (const [key, value] of Object.entries(merged)) if (value === undefined) delete merged[key];
  return merged as ProjectConfigValue;
}

/**
 * Saves one section of the project's configuration. It reads the project again right before writing,
 * merges the change into THAT configuration (not the one the page loaded some minutes ago), sends it, and
 * refreshes the project and its readiness.
 */
export function useProjectConfigPatch(slug: string) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function patch(change: ConfigChange, options: { name?: string; saved?: string } = {}): Promise<boolean> {
    setPending(true);
    setError(null);
    setMessage(null);
    try {
      const latest = await api.project(slug);
      const config = mergeConfig(latest.config as ProjectConfigValue, change);
      await api.updateProject(slug, { ...(options.name !== undefined ? { name: options.name } : {}), config });
      setMessage(options.saved ?? "Saved");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["project", slug] }),
        queryClient.invalidateQueries({ queryKey: ["readiness", slug] }),
      ]);
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
      return false;
    } finally {
      setPending(false);
    }
  }
  return { patch, pending, message, error };
}
