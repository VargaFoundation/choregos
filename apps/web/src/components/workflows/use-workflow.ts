// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/**
 * A project's workflow, by its name, and what the API says about it: the map and the process view.
 * The validation is the orchestrator's: what the console shows is what will apply.
 */
export function useWorkflow(slug: string, name: string) {
  const definition = useWorkflowDefinition(slug, name);
  const yaml = definition.data?.yaml;
  const validation = useQuery({
    queryKey: ["workflow-validation", slug, name, definition.data?.version],
    queryFn: () => api.validateWorkflow(yaml ?? ""),
    enabled: Boolean(yaml),
  });
  return { definition, validation };
}

/**
 * A project's workflow, by its name, without its validation: what a page reads from the document
 * alone (a ticket's page, its fields' titles, S55-14). Nothing is asked without a name; the cache is
 * the one `useWorkflow` fills.
 */
export function useWorkflowDefinition(slug: string, name: string | null | undefined) {
  return useQuery({
    queryKey: ["workflow", slug, name],
    queryFn: () => api.workflowNamed(slug, name ?? ""),
    enabled: Boolean(name),
  });
}
