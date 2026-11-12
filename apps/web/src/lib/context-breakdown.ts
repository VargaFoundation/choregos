// SPDX-License-Identifier: Apache-2.0
/**
 * What fills a run's model context (ADR 0141 §3-6), as the run page draws it: each section's tokens and
 * its share of the model's window, and the free space left — unknown when the catalogue does not know
 * the model's window. The total is the model's own count; the split between sections is estimated.
 */
import type { ContextBreakdown } from "@/lib/types";
import type { Hue } from "@/lib/workflow-hue";

type SectionKey = keyof ContextBreakdown["sections"];

/** The contract's sections, in its order, each with a name and a hue (identity, never a status: ADR 0120). */
export const CONTEXT_SECTIONS: readonly { key: SectionKey; label: string; hue: Hue }[] = [
  { key: "system", label: "System prompt", hue: 1 },
  { key: "tools.platform", label: "Platform tools", hue: 2 },
  { key: "tools.mcp", label: "MCP tools", hue: 3 },
  { key: "skills", label: "Skills", hue: 4 },
  { key: "knowledge", label: "Knowledge", hue: 5 },
  { key: "memory", label: "Memory", hue: 6 },
  { key: "messages", label: "Messages", hue: 7 },
  { key: "backend", label: "Added by the backend", hue: 8 },
];

export interface ContextRow {
  key: SectionKey;
  label: string;
  hue: Hue;
  tokens: number;
  /** Share of the window, or of the total when the window is unknown, between 0 and 1. */
  share: number;
}

export interface ContextView {
  total: number;
  /** The model's input window; `null` when the catalogue does not know it. */
  window: number | null;
  /** `window - total`, never below zero; `null` (unknown) without a window. */
  free: number | null;
  freeShare: number | null;
  /** The sections that hold tokens, in the contract's order. */
  rows: ContextRow[];
}

export function contextView(breakdown: ContextBreakdown): ContextView {
  const total = breakdown.prompt_tokens;
  const window = breakdown.max_input_tokens && breakdown.max_input_tokens > 0 ? breakdown.max_input_tokens : null;
  const scale = Math.max(window ?? 0, total, 1);
  const rows = CONTEXT_SECTIONS.map(({ key, label, hue }) => {
    const tokens = breakdown.sections[key] ?? 0;
    return { key, label, hue, tokens, share: tokens / scale };
  }).filter((row) => row.tokens > 0);
  const free = window === null ? null : Math.max(0, window - total);
  return { total, window, free, freeShare: free === null ? null : free / scale, rows };
}

/** A share as the table says it: one decimal, with a space before the sign. */
export function percent(share: number): string {
  return `${(Math.round(share * 1000) / 10).toFixed(1)} %`;
}
