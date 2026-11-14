// SPDX-License-Identifier: Apache-2.0
/** Activity entries in the contract's shape, for the Live tests. */
import type { ActivityEntry } from "@choregos/contracts/api";

export function run(id: string, overrides: Partial<ActivityEntry> = {}): ActivityEntry {
  return {
    kind: "run",
    id,
    status: "running",
    project_slug: "shop",
    work_item_id: "wi-1",
    work_item_key: "SHOP-42",
    workflow: "feature",
    stage_role: "implement",
    agent_slug: "coder",
    model: "a-model",
    started_at: "2026-11-12T10:00:00Z",
    ended_at: null,
    steps: 3,
    last_event: "tool_call",
    spend: {
      tokens_in: 12_000,
      tokens_out: 3_400,
      tokens_cached: 8_000,
      cost_usd: 0.42,
      read_at: "2026-11-12T10:04:00Z",
    },
    can_stop: true,
    ...overrides,
  };
}
