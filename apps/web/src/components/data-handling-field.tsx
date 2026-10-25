// SPDX-License-Identifier: Apache-2.0
"use client";

import { Input, Select } from "@/components/ui";
import { declaredHandling, dataHandlingProblems, type DataHandling } from "@/lib/data-handling";

const shown = (value: unknown, unit = ""): string =>
  value === undefined || value === null ? "unknown" : `${String(value)}${unit}`;

/**
 * What the organisation's contract says the search provider does with a query (ADR 0083 §3), as four
 * fields instead of raw JSON. Choregos' own declaration is shown beside them: the organisation can
 * only make it stricter, and must name the terms (`terms_ref`). "Not declared" leaves a value to the
 * backend's own declaration; an unknown value fails every bound that reads it.
 *
 * The API stays the judge: this refuses only what its `stricter` is known to refuse.
 */
export function DataHandlingField({
  id,
  type,
  value,
  disabled,
  onChange,
}: {
  id: string;
  type: string;
  value: unknown;
  disabled?: boolean;
  onChange: (value: unknown) => void;
}) {
  const current = (value && typeof value === "object" && !Array.isArray(value) ? value : {}) as DataHandling &
    Record<string, unknown>;
  const declared = declaredHandling(type);
  const problems = dataHandlingProblems(type, current);
  const set = (field: string, next: unknown) => {
    const merged: Record<string, unknown> = { ...current };
    if (next === undefined || next === "") delete merged[field];
    else merged[field] = next;
    onChange(Object.keys(merged).length > 0 ? merged : undefined);
  };
  return (
    <div className="space-y-2 rounded border border-line p-3" data-testid="data-handling">
      <p className="text-xs text-ink-muted" data-testid="data-handling-declared">
        Choregos knows this backend as: retention {shown(declared.query_retention_days, " days")}, reuse{" "}
        {declared.query_reuse == null ? "unknown" : declared.query_reuse ? "yes" : "no"}, region{" "}
        {shown(declared.region)}. Your declaration can only be stricter, and it must name the terms it comes from. An
        unknown value fails every bound that reads it.
      </p>
      <div className="grid gap-2 md:grid-cols-2">
        <label className="space-y-1" htmlFor={`${id}-days`}>
          <span className="text-xs text-ink-muted">Query retention (days)</span>
          <Input
            id={`${id}-days`}
            type="number"
            min={0}
            step={1}
            placeholder="Not declared (unknown)"
            value={current.query_retention_days ?? ""}
            disabled={disabled}
            onChange={(event) =>
              set("query_retention_days", event.target.value === "" ? undefined : Number(event.target.value))
            }
          />
        </label>
        <label className="space-y-1" htmlFor={`${id}-reuse`}>
          <span className="text-xs text-ink-muted">Queries reused (to train, rank or re-index)</span>
          <Select
            id={`${id}-reuse`}
            value={current.query_reuse === undefined || current.query_reuse === null ? "" : String(current.query_reuse)}
            disabled={disabled}
            onChange={(event) => set("query_reuse", event.target.value === "" ? undefined : event.target.value === "true")}
          >
            <option value="">Not declared (unknown)</option>
            <option value="false">No</option>
            <option value="true">Yes</option>
          </Select>
        </label>
        <label className="space-y-1" htmlFor={`${id}-region`}>
          <span className="text-xs text-ink-muted">Region where queries are processed</span>
          <Input
            id={`${id}-region`}
            placeholder="Not declared (unknown), e.g. eu"
            value={typeof current.region === "string" ? current.region : ""}
            disabled={disabled}
            onChange={(event) => set("region", event.target.value)}
          />
        </label>
        <label className="space-y-1" htmlFor={`${id}-terms`}>
          <span className="text-xs text-ink-muted">
            Terms reference<span aria-hidden> *</span>
            <span className="sr-only"> (required once anything is declared)</span>
          </span>
          <Input
            id={`${id}-terms`}
            placeholder="Contract ref, section, or dated URL"
            value={typeof current.terms_ref === "string" ? current.terms_ref : ""}
            disabled={disabled}
            onChange={(event) => set("terms_ref", event.target.value)}
          />
        </label>
      </div>
      {problems.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-danger" role="alert">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
