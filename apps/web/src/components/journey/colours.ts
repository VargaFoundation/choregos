// SPDX-License-Identifier: Apache-2.0
import type { StepStatus } from "./model";

/**
 * The colours of a work item's journey (ADR 0043): those of the states of an operations console:
 * blue for what runs, amber for what waits for a person, green for what is done, orange for what
 * is sent back, red for what failed, slate for what is not reached. The ink step of each scale: a
 * stroke at 3:1 at least on the page as on a card, in both themes (`tests/map-colours.test.ts`).
 * Colour never carries the meaning alone: every status is also said in so many words, and what
 * lives wears a ring.
 */
export const STATUS_COLOUR: Record<StepStatus, string> = {
  a_venir: "var(--choregos-neutral-solid)",
  en_cours: "var(--choregos-running-ink)",
  attend: "var(--choregos-waiting-ink)",
  fait: "var(--choregos-succeeded-ink)",
  renvoye: "var(--choregos-retrying-ink)",
  echoue: "var(--choregos-failed-ink)",
};

/** A box's fill: tinted when it lives, the page's otherwise. */
export function statusFill(status: StepStatus): string {
  if (status === "en_cours") return "var(--choregos-running-soft)";
  if (status === "attend") return "var(--choregos-waiting-soft)";
  return "var(--choregos-surface)";
}

/** A taken send-back (a review that sends back, a retry): the arc, its head and its round count. */
export const SENT_BACK_COLOUR = "var(--choregos-retrying-ink)";
