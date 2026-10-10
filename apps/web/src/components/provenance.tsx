// SPDX-License-Identifier: Apache-2.0
import { CircleHelp, Eye, MessageSquareQuote, Sigma, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";
import type { Provenance as Genre } from "@/lib/provenance";

const ICONES: Record<Genre, LucideIcon> = { declared: MessageSquareQuote, observed: Eye, inferred: Sigma, unknown: CircleHelp };

/** Ce que veut dire chaque provenance, pour l'infobulle et pour un lecteur d'écran. */
export const SENS: Record<Genre, string> = {
  declared: "said by the agent, not checked by the platform",
  observed: "measured or seen by the platform itself",
  inferred: "computed from other signals",
  unknown: "the source is not known",
};

/** L'étiquette de provenance d'un signal (ADR 0045) : un mot, une icône, et ce qu'il veut dire. */
export function Provenance({ de, className }: { de: Genre; className?: string }) {
  const Icone = ICONES[de];
  return (
    <span
      data-provenance={de}
      title={SENS[de]}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 font-mono text-[10px] tracking-[0.06em] uppercase",
        de === "declared" ? "text-waiting-ink" : "text-ink-muted",
        className,
      )}
    >
      <Icone aria-hidden className="size-3" strokeWidth={2} />
      {de}
      <span className="sr-only"> — {SENS[de]}</span>
    </span>
  );
}
