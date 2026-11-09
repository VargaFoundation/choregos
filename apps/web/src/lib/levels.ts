// SPDX-License-Identifier: Apache-2.0
/**
 * The scales the console draws (ADR 0129): an ordered list of values, each with a tone from a closed
 * list. The platform's own scales live here for a ticket's columns, which are not artifacts.
 */
import type { Tone } from "@/components/ui";

export type Level = { value: string; tone?: "neutral" | "info" | "ok" | "warn" | "danger"; title?: string | null };

export const RISK_LEVELS: Level[] = [
  { value: "low", tone: "ok" },
  { value: "medium", tone: "warn" },
  { value: "high", tone: "danger" },
];

/** A size is neither good nor bad: its scale is neutral, and its position says how large it is. */
export const SIZE_LEVELS: Level[] = [{ value: "S" }, { value: "M" }, { value: "L" }, { value: "XL" }];

export function toneOfRisk(risk: string | null | undefined): Tone {
  return RISK_LEVELS.find((level) => level.value === risk)?.tone ?? "neutral";
}

/** A scale with no judgement in it: every level neutral. Its rank is drawn rather than coloured. */
export function isRankOnly(levels: Level[]): boolean {
  return levels.every((level) => (level.tone ?? "neutral") === "neutral");
}

/** The word a level is read as: its title, or its value with a capital unless it is a code like `XL`. */
export function levelLabel(level: Level): string {
  if (level.title) return level.title;
  return `${level.value.charAt(0).toUpperCase()}${level.value.slice(1)}`;
}
