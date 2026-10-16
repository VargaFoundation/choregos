// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { Badge } from "@/components/ui";
import { GROUP_LABELS, GROUP_ORDER, type LineChange, type SpecChange } from "@/lib/agent-diff";

/**
 * The structured difference between two versions (S40-12), as words and signs, never colour alone: each
 * change says "Added", "Removed" or "Changed from … to …" and carries a `+`, a `−` or a `~`. Shared by
 * the compare view and the builder's Changes panel.
 */

const SIGN = { added: "+", removed: "−", changed: "~" } as const;
const WORD = { added: "Added", removed: "Removed", changed: "Changed" } as const;
const TONE = { added: "ok", removed: "danger", changed: "warn" } as const;

/** Context kept around a changed line; longer runs of unchanged lines fold. */
const CONTEXT = 2;

type Row = { line: LineChange } | { folded: number };

function rows(lines: LineChange[]): Row[] {
  const keep = lines.map((line, i) =>
    line.kind !== "same" || lines.slice(Math.max(0, i - CONTEXT), i + CONTEXT + 1).some((near) => near.kind !== "same"),
  );
  const out: Row[] = [];
  let skipped = 0;
  lines.forEach((line, i) => {
    if (keep[i]) {
      if (skipped) out.push({ folded: skipped });
      skipped = 0;
      out.push({ line });
    } else skipped++;
  });
  if (skipped) out.push({ folded: skipped });
  return out;
}

function Lines({ lines }: { lines: LineChange[] }) {
  return (
    <pre className="whitespace-pre-wrap break-words rounded bg-surface-muted p-3 text-xs" aria-label="Changed instructions">
      {rows(lines).map((row, i) =>
        "folded" in row ? (
          <div key={i} className="text-ink-muted">
            {`… ${row.folded} unchanged line${row.folded === 1 ? "" : "s"}`}
          </div>
        ) : (
          <div key={i} className={row.line.kind === "same" ? "text-ink-muted" : "font-medium text-ink"}>
            <span aria-hidden="true">{row.line.kind === "added" ? "+ " : row.line.kind === "removed" ? "− " : "  "}</span>
            {row.line.kind !== "same" && <span className="sr-only">{row.line.kind === "added" ? "Added line: " : "Removed line: "}</span>}
            {row.line.text || " "}
          </div>
        ),
      )}
    </pre>
  );
}

function Change({ change }: { change: SpecChange }) {
  const [whole, setWhole] = useState(false);
  return (
    <li className="space-y-1.5 text-sm" data-testid={`change-${change.field}`}>
      <div className="flex flex-wrap items-baseline gap-2">
        <span aria-hidden="true" className="font-mono">
          {SIGN[change.kind]}
        </span>
        <Badge tone={TONE[change.kind]}>{WORD[change.kind]}</Badge>
        <span className="font-medium">{change.label}</span>
        {!change.lines && !change.tooLong && (
          <span className="text-ink-muted">
            {change.kind === "changed" ? (
              <>
                from <code>{change.before}</code> to <code>{change.after}</code>
              </>
            ) : (
              <code>{change.kind === "added" ? change.after : change.before}</code>
            )}
            {change.detail && <span className="ml-2 font-mono text-xs">{change.detail}</span>}
          </span>
        )}
      </div>
      {change.lines && <Lines lines={change.lines} />}
      {change.tooLong && (
        <div className="space-y-1.5">
          <p className="text-xs text-ink-muted">Too long to compare line by line.</p>
          <button type="button" className="text-xs underline" aria-expanded={whole} onClick={() => setWhole(!whole)}>
            {whole ? "Hide both texts" : "Show both texts"}
          </button>
          {whole && (
            <div className="grid gap-2 md:grid-cols-2">
              {/* A long text scrolls: it must take the keyboard focus to be scrolled without a mouse. */}
              {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
              <pre className="max-h-96 overflow-auto rounded bg-surface-muted p-3 text-xs" aria-label="Before" tabIndex={0}>
                {change.before}
              </pre>
              {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
              <pre className="max-h-96 overflow-auto rounded bg-surface-muted p-3 text-xs" aria-label="After" tabIndex={0}>
                {change.after}
              </pre>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

/** The changes, grouped as the read view groups a version; empty says so. */
export function ChangeList({ changes, empty = "No difference." }: { changes: SpecChange[]; empty?: string }) {
  if (changes.length === 0) return <p className="text-sm text-ink-muted">{empty}</p>;
  return (
    <div className="space-y-4" data-testid="changes">
      {GROUP_ORDER.map((group) => {
        const inGroup = changes.filter((c) => c.group === group);
        if (inGroup.length === 0) return null;
        return (
          <section key={group} aria-label={GROUP_LABELS[group]} className="space-y-2">
            <h3 className="text-xs font-medium text-ink-muted">{GROUP_LABELS[group]}</h3>
            <ul className="space-y-3">
              {inGroup.map((change) => (
                <Change key={`${change.field}-${change.label}-${change.kind}`} change={change} />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
