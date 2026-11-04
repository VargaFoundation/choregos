// SPDX-License-Identifier: Apache-2.0
"use client";

import type { ReactNode } from "react";
import { Badge, Dot, type Tone } from "@/components/ui";
import { shortDate } from "@/lib/format";
import type { HandOff, HandOffEvent } from "@/lib/types";

const NAMES: Record<string, string> = {
  spec_markdown: "Specification",
  plan_markdown: "Plan",
  review_markdown: "Review",
  release_notes_markdown: "Release notes",
};

/** The start of a fingerprint: enough to recognise it, not enough to copy it. */
export function courte(digest: string | null | undefined): string {
  return digest ? digest.replace(/^sha256:/, "").slice(0, 12) : "—";
}

export type Lecture = "same" | "other" | "unknown";

/**
 * What a read read, compared with the productions: the revision produced just before it ("same"),
 * another revision that a step produced ("other"), or a revision that no step produced
 * (edited by hand, "unknown").
 */
export function lecture(passage: HandOff, rang: number): Lecture {
  const lue = passage.events[rang]!;
  const avant = passage.events.slice(0, rang).filter((e) => e.kind === "produced").at(-1);
  if (avant && avant.digest === lue.digest) return "same";
  return passage.events.some((e) => e.kind === "produced" && e.digest === lue.digest) ? "other" : "unknown";
}

/** Has the output changed since its last production? */
export function editedAfterTheFact(passage: HandOff): boolean {
  const latest = passage.events.filter((e) => e.kind === "produced").at(-1);
  return Boolean(latest) && passage.current_digest !== latest!.digest;
}

const LECTURES: Record<Lecture, [Tone, string]> = {
  same: ["succeeded", "The revision produced"],
  other: ["failed", "Another revision"],
  unknown: ["waiting", "A revision no step produced"],
};

/**
 * A ticket's hand-off receipts (S25-04): for each step output, who produced it, who
 * read it, under which fingerprint, and whether it has changed since. What the platform itself recorded.
 */
export function Passages({ passages }: { passages: HandOff[] }) {
  return (
    <ul aria-label="Hand-offs" className="space-y-4" data-testid="passages">
      {passages.map((passage) => (
        <li key={passage.output} data-output={passage.output} className="space-y-1.5">
          <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
            <span className="font-medium text-ink">{NAMES[passage.output] ?? passage.output.replace(/_/g, " ")}</span>{" "}
            <code className="font-mono text-xs text-ink-muted" title={passage.current_digest ?? undefined}>
              {courte(passage.current_digest)}
            </code>{" "}
            {editedAfterTheFact(passage) && (
              <Badge tone="waiting" data-testid="modified-afterwards">
                {passage.current_digest ? "Modified after it was produced" : "Removed after it was produced"}
              </Badge>
            )}
          </p>
          <ol className="space-y-1 border-l border-line pl-3 text-xs">
            {passage.events.map((evenement, rang) => (
              <Row key={`${evenement.kind}-${evenement.run_id}-${evenement.digest}`} evenement={evenement}>
                {evenement.kind === "read" && <Verdict lecture={lecture(passage, rang)} />}
              </Row>
            ))}
          </ol>
        </li>
      ))}
    </ul>
  );
}

function Row({ evenement, children }: { evenement: HandOffEvent; children?: ReactNode }) {
  const qui = [evenement.stage ?? evenement.run_id, evenement.attempt ? `attempt ${evenement.attempt}` : null]
    .filter(Boolean)
    .join(" · ");
  return (
    <li className="flex flex-wrap items-center gap-x-2 gap-y-1" data-kind={evenement.kind}>
      <Dot tone={evenement.kind === "produced" ? "agent" : "neutral"} />
      {/* Spaces in the text, not only the flex gap: a screen reader reads the text. */}
      <span className="text-ink-muted">{evenement.kind === "produced" ? "Produced by" : "Read by"}</span>{" "}
      <span className="text-ink">{qui}</span>{" "}
      <code className="font-mono text-ink-muted" title={evenement.digest}>
        {courte(evenement.digest)}
      </code>{" "}
      <span className="font-mono text-ink-muted">{shortDate(evenement.at)}</span> {children}
    </li>
  );
}

function Verdict({ lecture: genre }: { lecture: Lecture }) {
  const [ton, libelle] = LECTURES[genre];
  return (
    <Badge tone={ton} data-reading={genre}>
      {libelle}
    </Badge>
  );
}
