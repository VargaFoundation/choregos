// SPDX-License-Identifier: Apache-2.0
import { Badge } from "@/components/ui";
import { cn } from "@/lib/cn";
import { isRankOnly, type Level, levelLabel } from "@/lib/levels";

/**
 * One value of an ordered scale (ADR 0129). A scale that judges is tinted; a scale that only ranks
 * — a size — stays neutral and shows its position. The word is always written: never colour alone.
 * A value off its scale is shown as it is, neutral, rather than dropped.
 */
export function LevelBadge({
  value,
  levels,
  ...props
}: {
  value: string;
  levels: Level[];
  "data-testid"?: string;
}) {
  const rank = levels.findIndex((level) => level.value === value);
  const level = rank >= 0 ? levels[rank]! : undefined;
  const ranked = level !== undefined && isRankOnly(levels);
  return (
    <Badge
      tone={level?.tone ?? "neutral"}
      dot={!ranked}
      data-verbatim
      data-level={value}
      aria-label={ranked ? `${levelLabel(level)}, ${rank + 1} of ${levels.length}` : undefined}
      {...props}
    >
      {ranked && (
        <span aria-hidden className="inline-flex items-end gap-px" data-testid="level-rank">
          {levels.map((step, index) => (
            <span
              key={step.value}
              style={{ height: `${4 + index * 2}px` }}
              className={cn("w-0.5 rounded-sm", index <= rank ? "bg-neutral-ink" : "bg-neutral-line")}
            />
          ))}
        </span>
      )}
      {level ? levelLabel(level) : value}
    </Badge>
  );
}
