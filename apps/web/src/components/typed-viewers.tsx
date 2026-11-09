// SPDX-License-Identifier: Apache-2.0
"use client";

import { useMemo, useState } from "react";
import { ArtifactValue, metricText } from "@/components/artifact-value";
import { Empty, ErrorNote } from "@/components/ui";
import { parseCsv } from "@/lib/csv-parse";
import type { Artifact } from "@/lib/types";

const MAX_ROWS = 500;

/** A bounded metric's position, when it has both bounds and is not a model's opinion of itself. */
function Meter({ artifact }: { artifact: Artifact }) {
  const { display, value, name } = artifact;
  if (typeof value !== "number" || display?.min == null || display.max == null || name === "confidence") return null;
  const share = Math.round(((value - display.min) / (display.max - display.min)) * 100);
  return (
    <div
      role="meter"
      aria-label={name}
      aria-valuemin={display.min}
      aria-valuemax={display.max}
      aria-valuenow={value}
      aria-valuetext={metricText(value, display.unit)}
      className="h-1.5 w-48 overflow-hidden rounded-full bg-surface-muted"
    >
      <div className="h-full bg-accent" style={{ width: `${Math.min(Math.max(share, 0), 100)}%` }} />
    </div>
  );
}

/** A level or a metric on its own tab. */
export function ValueViewer({ artifact }: { artifact: Artifact }) {
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm" data-testid="artifact-typed-value">
      <ArtifactValue artifact={artifact} />
      <Meter artifact={artifact} />
    </div>
  );
}

export function KeyValueViewer({ text }: { text: string }) {
  let entries: [string, unknown][];
  try {
    entries = Object.entries(JSON.parse(text) as Record<string, unknown>);
  } catch {
    return <ErrorNote>This key/value output is not valid JSON.</ErrorNote>;
  }
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm" data-testid="artifact-keyvalue">
      {entries.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="font-mono text-xs text-ink-muted" data-verbatim>
            {key}
          </dt>
          <dd className="min-w-0 break-words" data-verbatim>
            {String(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function CsvTable({ text }: { text: string }) {
  const csv = useMemo(() => parseCsv(text, MAX_ROWS), [text]);
  if (csv.header.length === 0) return <Empty>This file is empty.</Empty>;
  return (
    <div className="space-y-1" data-testid="artifact-csv">
      <div className="max-h-[32rem] overflow-auto">
        <table aria-label="csv rows">
          <thead>
            <tr>
              {csv.header.map((cell, index) => (
                <th key={index} data-verbatim>
                  {cell}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {csv.rows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c} className="font-mono text-xs" data-verbatim>
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {csv.truncated && (
        <p className="text-xs text-ink-muted" data-testid="artifact-csv-truncated">
          The first {MAX_ROWS} rows of {csv.total}: download the file for the rest.
        </p>
      )}
    </div>
  );
}

function Scalar({ value }: { value: unknown }) {
  if (value === null) return <span className="text-ink-muted">null</span>;
  if (typeof value === "string") return <span className="text-succeeded-ink">&quot;{value}&quot;</span>;
  return <span className="text-info">{String(value)}</span>;
}

function Node({ name, value, depth }: { name: string | null; value: unknown; depth: number }) {
  const [open, setOpen] = useState(depth < 2);
  const label = name === null ? null : <span className="text-ink-muted">{name}: </span>;
  if (typeof value !== "object" || value === null) {
    return (
      <div data-verbatim>
        {label}
        <Scalar value={value} />
      </div>
    );
  }
  const entries: [string, unknown][] = Array.isArray(value) ? value.map((v, i) => [String(i), v]) : Object.entries(value);
  const [opening, closing] = Array.isArray(value) ? ["[", "]"] : ["{", "}"];
  return (
    <div>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="min-h-5 text-left" data-verbatim>
        <span aria-hidden className="inline-block w-3 text-ink-muted">
          {open ? "▾" : "▸"}
        </span>
        {label}
        {opening}
        {!open && <span className="text-ink-muted"> {entries.length} … </span>}
        {!open && closing}
      </button>
      {open && (
        <div className="ml-3 border-l border-line pl-2">
          {entries.map(([key, child]) => (
            <Node key={key} name={key} value={child} depth={depth + 1} />
          ))}
        </div>
      )}
      {open && <div>{closing}</div>}
    </div>
  );
}

/** JSON as a tree a reader folds; what does not parse is shown as it came. */
export function JsonTree({ text }: { text: string }) {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return (
      <pre className="max-h-[32rem] overflow-auto border border-line p-2 font-mono text-xs" data-testid="artifact-text">
        {text}
      </pre>
    );
  }
  return (
    <div className="max-h-[32rem] overflow-auto border border-line p-2 font-mono text-xs" data-testid="artifact-json">
      <Node name={null} value={value} depth={0} />
    </div>
  );
}
