// SPDX-License-Identifier: Apache-2.0
"use client";

/**
 * The memories an agent version binds (S40-11, ADR 0075): an ordered list, each memory read or read and
 * written, at most one written. Earlier wins when two memories hold the same subject. A `project`
 * memory means "of whichever project the run is in"; a global one is the organisation's, and what a run
 * writes to it is only ever a proposal an administrator accepts.
 */
import { useState } from "react";
import { Button, Select } from "@/components/ui";
import {
  IMPLICIT,
  MAX_BINDINGS,
  describe,
  labelOf,
  moveBinding,
  projectsHaving,
  reachLabel,
  withWriter,
  type Binding,
} from "@/lib/memories";
import type { MemoryStore } from "@/lib/types";

export interface MemoryOptions {
  /** The organisation's global memories. */
  global: MemoryStore[];
  /** Every project memory of the organisation (a name may exist in several projects). */
  project: MemoryStore[];
}

export function MemoryBindingsEditor({
  value,
  onChange,
  options,
}: {
  value: Binding[];
  onChange: (next: Binding[]) => void;
  options: MemoryOptions;
}) {
  const [adding, setAdding] = useState("");
  const known = [...options.global, ...options.project];
  const bound = new Set(value.map(labelOf));
  const namedProject = [...new Set(options.project.filter((m) => m.status !== "archived").map((m) => m.slug))];
  const offered = (scope: "global" | "project", slug: string) => !bound.has(`${scope}:${slug}`);
  const full = value.length >= MAX_BINDINGS;

  function add() {
    const [scope, slug] = adding.split(":") as ["global" | "project", string];
    if (!scope || !slug) return;
    onChange([...value, { scope, slug, mode: "read" }]);
    setAdding("");
  }

  return (
    <div className="space-y-3" data-testid="memory-bindings">
      <p className="text-xs text-ink-muted">
        Memories are read in this order: when two hold the same subject the earlier one wins, and each gives what fits the
        token budget in turn. One memory at most is written to; what a run writes to an organisation memory is a proposal
        that waits for an administrator. Memory is data in the context, never instructions.
      </p>
      {value.length === 0 ? (
        <p className="text-xs text-ink-muted" data-testid="memory-bindings-empty">
          None — a run neither reads nor writes what earlier runs learnt.
        </p>
      ) : (
        <ol className="space-y-2">
          {value.map((b, index) => (
            <li
              key={labelOf(b)}
              className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded border border-line px-3 py-2 text-sm"
              data-testid="memory-binding"
            >
              <span className="w-5 text-xs text-ink-muted">{index + 1}.</span>
              <span className="min-w-0 flex-1">
                <span className="block">{describe(b, known)}</span>
                <span className="block font-mono text-xs text-ink-muted">{labelOf(b)}</span>
              </span>
              <fieldset className="flex items-center gap-3">
                <legend className="sr-only">What {labelOf(b)} is used for</legend>
                <label className="inline-flex items-center gap-1 text-xs">
                  <input
                    type="radio"
                    name={`mode-${labelOf(b)}`}
                    checked={b.mode !== "write"}
                    onChange={() => onChange(value.map((x, i) => (i === index ? { ...x, mode: "read" } : x)))}
                  />
                  Read
                </label>
                <label className="inline-flex items-center gap-1 text-xs">
                  <input
                    type="radio"
                    name={`mode-${labelOf(b)}`}
                    checked={b.mode === "write"}
                    onChange={() => onChange(withWriter(value, index))}
                  />
                  Read and write
                </label>
              </fieldset>
              <span className="flex items-center gap-1">
                <Button
                  size="sm"
                  aria-label={`Move ${labelOf(b)} up`}
                  disabled={index === 0}
                  onClick={() => onChange(moveBinding(value, index, -1))}
                >
                  <span aria-hidden="true">↑</span>
                </Button>
                <Button
                  size="sm"
                  aria-label={`Move ${labelOf(b)} down`}
                  disabled={index === value.length - 1}
                  onClick={() => onChange(moveBinding(value, index, 1))}
                >
                  <span aria-hidden="true">↓</span>
                </Button>
                <Button size="sm" aria-label={`Remove ${labelOf(b)}`} onClick={() => onChange(value.filter((_, i) => i !== index))}>
                  Remove
                </Button>
              </span>
            </li>
          ))}
        </ol>
      )}
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">Add a memory</span>
          <Select value={adding} onChange={(e) => setAdding(e.target.value)} disabled={full}>
            <option value="">—</option>
            <optgroup label="This project (whichever project the run is in)">
              {(Object.keys(IMPLICIT) as Array<keyof typeof IMPLICIT>)
                .filter((slug) => offered("project", slug))
                .map((slug) => (
                  <option key={slug} value={`project:${slug}`}>
                    {IMPLICIT[slug].label}
                  </option>
                ))}
              {namedProject
                .filter((slug) => offered("project", slug))
                .map((slug) => (
                  <option key={slug} value={`project:${slug}`}>
                    {slug} — exists in {projectsHaving(slug, options.project).join(", ")}
                  </option>
                ))}
            </optgroup>
            <optgroup label="Organisation">
              {options.global
                .filter((m) => m.status !== "archived" && offered("global", m.slug))
                .map((m) => (
                  <option key={m.slug} value={`global:${m.slug}`}>
                    {m.display_name} — {reachLabel(m)}
                  </option>
                ))}
            </optgroup>
          </Select>
        </label>
        <Button size="sm" disabled={!adding || full} onClick={add}>
          Add it
        </Button>
        {full && <span className="text-xs text-ink-muted">At most {MAX_BINDINGS} memories.</span>}
      </div>
    </div>
  );
}
