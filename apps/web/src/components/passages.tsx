// SPDX-License-Identifier: Apache-2.0
"use client";

import type { ReactNode } from "react";
import { Badge, Dot, type Tone } from "@/components/ui";
import { shortDate } from "@/lib/format";
import type { HandOff, HandOffEvent } from "@/lib/types";

const NOMS: Record<string, string> = {
  spec_markdown: "specification",
  plan_markdown: "plan",
  review_markdown: "review",
  release_notes_markdown: "release notes",
};

/** Le début d'une empreinte : de quoi la reconnaître, pas de quoi la recopier. */
export function courte(digest: string | null | undefined): string {
  return digest ? digest.replace(/^sha256:/, "").slice(0, 12) : "—";
}

export type Lecture = "same" | "other" | "unknown";

/**
 * Ce qu'une lecture a lu, comparé aux productions : la révision produite juste avant elle (« same »),
 * une autre révision qu'une étape a produite (« other »), ou une révision qu'aucune étape n'a produite
 * — modifiée à la main (« unknown »).
 */
export function lecture(passage: HandOff, rang: number): Lecture {
  const lue = passage.events[rang]!;
  const avant = passage.events.slice(0, rang).filter((e) => e.kind === "produced").at(-1);
  if (avant && avant.digest === lue.digest) return "same";
  return passage.events.some((e) => e.kind === "produced" && e.digest === lue.digest) ? "other" : "unknown";
}

/** La sortie a-t-elle changé depuis sa dernière production ? */
export function modifieeApresCoup(passage: HandOff): boolean {
  const derniere = passage.events.filter((e) => e.kind === "produced").at(-1);
  return Boolean(derniere) && passage.current_digest !== derniere!.digest;
}

const LECTURES: Record<Lecture, [Tone, string]> = {
  same: ["succeeded", "the revision produced"],
  other: ["failed", "another revision"],
  unknown: ["waiting", "a revision no step produced"],
};

/**
 * Les reçus de passage d'un ticket (S25-04) : pour chaque sortie d'étape, qui l'a produite, qui l'a
 * lue, sous quelle empreinte — et si elle a changé depuis. Ce que la plateforme a consigné elle-même.
 */
export function Passages({ passages }: { passages: HandOff[] }) {
  return (
    <ul aria-label="hand-offs" className="space-y-4" data-testid="passages">
      {passages.map((passage) => (
        <li key={passage.output} data-sortie={passage.output} className="space-y-1.5">
          <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
            <span className="font-medium text-ink">{NOMS[passage.output] ?? passage.output.replace(/_/g, " ")}</span>{" "}
            <code className="font-mono text-xs text-ink-muted" title={passage.current_digest ?? undefined}>
              {courte(passage.current_digest)}
            </code>{" "}
            {modifieeApresCoup(passage) && (
              <Badge tone="waiting" data-testid="modifiee-apres-coup">
                {passage.current_digest ? "modified after it was produced" : "removed after it was produced"}
              </Badge>
            )}
          </p>
          <ol className="space-y-1 border-l border-line pl-3 text-xs">
            {passage.events.map((evenement, rang) => (
              <Ligne key={`${evenement.kind}-${evenement.run_id}-${evenement.digest}`} evenement={evenement}>
                {evenement.kind === "read" && <Verdict lecture={lecture(passage, rang)} />}
              </Ligne>
            ))}
          </ol>
        </li>
      ))}
    </ul>
  );
}

function Ligne({ evenement, children }: { evenement: HandOffEvent; children?: ReactNode }) {
  const qui = [evenement.stage ?? evenement.run_id, evenement.attempt ? `attempt ${evenement.attempt}` : null]
    .filter(Boolean)
    .join(" · ");
  return (
    <li className="flex flex-wrap items-center gap-x-2 gap-y-1" data-genre={evenement.kind}>
      <Dot tone={evenement.kind === "produced" ? "agent" : "neutral"} />
      {/* Des espaces dans le texte, pas seulement l'écart du flex : un lecteur d'écran lit le texte. */}
      <span className="text-ink-muted">{evenement.kind === "produced" ? "produced by" : "read by"}</span>{" "}
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
    <Badge tone={ton} data-lecture={genre}>
      {libelle}
    </Badge>
  );
}
