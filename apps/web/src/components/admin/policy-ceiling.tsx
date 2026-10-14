// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Card, ErrorNote, Textarea, buttonClasses } from "@/components/ui";
import { api } from "@/lib/api";

/** The JSON a person types, or `null` to remove the ceiling; an error message when it is not JSON. */
export function parseCeiling(text: string): { ceiling: Record<string, unknown> | null } | { error: string } {
  if (!text.trim()) return { ceiling: null };
  try {
    const value: unknown = JSON.parse(text);
    if (value === null || typeof value !== "object" || Array.isArray(value)) return { error: "a ceiling is a JSON object" };
    return { ceiling: value as Record<string, unknown> };
  } catch (cause) {
    return { error: cause instanceof Error ? cause.message : "not JSON" };
  }
}

/**
 * The organisation's policy ceiling (S27-05): caps every project policy stays under — budgets, turns,
 * minutes, attempts, tool calls, scope sizes. A project policy above it is refused when saved; one
 * saved before runs clamped, and its page says what was clamped.
 */
export function PolicyCeilingCard({ org, canEdit }: { org: string; canEdit: boolean }) {
  const client = useQueryClient();
  const ceiling = useQuery({ queryKey: ["policy-ceiling", org], queryFn: () => api.policyCeiling(org) });
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const shown = text ?? (ceiling.data?.ceiling ? JSON.stringify(ceiling.data.ceiling, null, 2) : "");
  async function save() {
    setError(null);
    const parsed = parseCeiling(shown);
    if ("error" in parsed) return setError(parsed.error);
    try {
      await api.putPolicyCeiling(org, parsed.ceiling);
      setText(null);
      await client.invalidateQueries({ queryKey: ["policy-ceiling", org] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    }
  }
  return (
    <Card title="policy ceiling">
      <div className="space-y-2 text-sm">
        <p className="text-ink-muted">
          Caps every project policy of {org} stays under. Example:{" "}
          <code>{'{"budgets": {"ticket_usd": {"M": 20}, "max_turns": {"default": 60}}, "attempts": {"default_max": 2}}'}</code>
        </p>
        <Textarea aria-label="policy ceiling" rows={8} value={shown} readOnly={!canEdit} onChange={(e) => setText(e.target.value)} />
        {ceiling.data?.updated_by && (
          <p className="text-xs text-ink-muted">
            set by {ceiling.data.updated_by} {ceiling.data.updated_at ? `on ${ceiling.data.updated_at.slice(0, 10)}` : ""}
          </p>
        )}
        {canEdit && (
          <button type="button" className={buttonClasses("secondary", "sm")} onClick={() => void save()}>
            save the ceiling
          </button>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Card>
  );
}
