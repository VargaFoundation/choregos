// SPDX-License-Identifier: Apache-2.0
import { api } from "@/lib/api";
import type { UsableModel } from "@/lib/types";

/**
 * The models an agent may be given in this organisation (the catalogue of `/admin/models`, ADR 0060).
 * An API that predates the catalogue answers 404: the gateway's own list stands in, unfiltered.
 */
export async function usableModels(org: string, query: { role?: string; usage?: string } = {}): Promise<UsableModel[]> {
  try {
    return await api.usableModels(org, query);
  } catch {
    const listed = await api.platformModels();
    return listed.map((m) => ({
      model_name: m.model_name,
      display_name: m.model_name,
      provider: m.provider,
      input_cost_per_1k: m.input_cost_per_1k,
      output_cost_per_1k: m.output_cost_per_1k,
      priced_by: m.input_cost_per_1k != null ? ("gateway" as const) : ("none" as const),
      tiers: [],
    }));
  }
}
