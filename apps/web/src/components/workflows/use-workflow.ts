// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/**
 * Un workflow du projet, par son nom, et ce que l'API en dit : la carte et la vue processus.
 * La validation est celle de l'orchestrateur — ce que la console montre est ce qui s'appliquera.
 */
export function useWorkflow(slug: string, name: string) {
  const definition = useQuery({
    queryKey: ["workflow", slug, name],
    queryFn: () => api.workflowNamed(slug, name),
  });
  const yaml = definition.data?.yaml;
  const validation = useQuery({
    queryKey: ["workflow-validation", slug, name, definition.data?.version],
    queryFn: () => api.validateWorkflow(yaml ?? ""),
    enabled: Boolean(yaml),
  });
  return { definition, validation };
}
