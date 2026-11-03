// SPDX-License-Identifier: Apache-2.0
import { CircleHelp, Eye, MessageSquareQuote, Sigma, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";
import { toSentenceCase } from "@/lib/format";
import type { Provenance as Genre } from "@/lib/provenance";

const ICONES: Record<Genre, LucideIcon> = { declared: MessageSquareQuote, observed: Eye, inferred: Sigma, unknown: CircleHelp };

/** What each provenance means, for the tooltip and for a screen reader. */
export const SENS: Record<Genre, string> = {
  declared: "said by the agent, not checked by the platform",
  observed: "measured or seen by the platform itself",
  inferred: "computed from other signals",
  unknown: "the source is not known",
};

/** A signal's provenance label (ADR 0045): a word, an icon, and what it means. */
export function Provenance({ de, className }: { de: Genre; className?: string }) {
  const Icone = ICONES[de];
  return (
    <span
      data-provenance={de}
      title={SENS[de]}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 text-[11px] font-medium",
        de === "declared" ? "text-waiting-ink" : "text-ink-muted",
        className,
      )}
    >
      <Icone aria-hidden className="size-3" strokeWidth={2} />
      {toSentenceCase(de)}
      <span className="sr-only"> — {SENS[de]}</span>
    </span>
  );
}
