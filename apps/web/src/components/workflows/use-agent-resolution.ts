// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "@/lib/api";
import type { AgentResolution, ProcessStep } from "@/lib/types";

/**
 * Which agent version a run would load, for each registry agent the process names (S39-13). The refs
 * come from the (draft) process steps, so an unpublished draft resolves too. Empty until answered,
 * and empty against an API that predates the endpoint: the step card then falls back to the actor's
 * name.
 */
export function useAgentResolution(slug: string, process: ProcessStep[] | undefined): Map<string, AgentResolution> {
  const refs = useMemo(
    () => [...new Set((process ?? []).map((step) => step.agent).filter((ref): ref is string => Boolean(ref)))].sort(),
    [process],
  );
  const reponse = useQuery({
    queryKey: ["agent-resolution", slug, refs],
    queryFn: () => api.resolveProjectAgents(slug, refs),
    enabled: refs.length > 0,
    retry: false,
  });
  return useMemo(() => new Map((reponse.data ?? []).map((r) => [r.ref, r])), [reponse.data]);
}
