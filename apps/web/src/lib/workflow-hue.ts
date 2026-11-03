// SPDX-License-Identifier: Apache-2.0
/**
 * Colour that orients (ADR 0120): eight categorical hues for identity — a workflow, a section of the
 * navigation, a group of settings —, never for a status and never for what can be clicked.
 *
 * A workflow's hue is computed from its name, so the same workflow wears the same colour on the
 * board, a ticket, the workflow list and the map, with nothing to configure.
 */

export type Hue = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8;

export const HUES: readonly Hue[] = [1, 2, 3, 4, 5, 6, 7, 8];

/** The classes of a hue. Written out in full: Tailwind only generates the classes it reads. */
export interface HueClasses {
  /** A solid fill: a dot, a swatch, a stripe. */
  solid: string;
  /** A tinted background. */
  soft: string;
  /** A border in the hue. */
  line: string;
  /** Text in the hue (4.5:1 on every surface and on `soft`, `theme-contraste.test.ts`). */
  ink: string;
  /** A pill: tinted background, border and text. */
  pill: string;
  /** A top stripe (`border-t-*`): the edge of a column or a section. */
  stripe: string;
}

export const HUE_CLASSES: Record<Hue, HueClasses> = {
  1: {
    solid: "bg-cat-1",
    soft: "bg-cat-1-soft",
    line: "border-cat-1-line",
    ink: "text-cat-1-ink",
    pill: "border-cat-1-line bg-cat-1-soft text-cat-1-ink",
    stripe: "border-t-cat-1",
  },
  2: {
    solid: "bg-cat-2",
    soft: "bg-cat-2-soft",
    line: "border-cat-2-line",
    ink: "text-cat-2-ink",
    pill: "border-cat-2-line bg-cat-2-soft text-cat-2-ink",
    stripe: "border-t-cat-2",
  },
  3: {
    solid: "bg-cat-3",
    soft: "bg-cat-3-soft",
    line: "border-cat-3-line",
    ink: "text-cat-3-ink",
    pill: "border-cat-3-line bg-cat-3-soft text-cat-3-ink",
    stripe: "border-t-cat-3",
  },
  4: {
    solid: "bg-cat-4",
    soft: "bg-cat-4-soft",
    line: "border-cat-4-line",
    ink: "text-cat-4-ink",
    pill: "border-cat-4-line bg-cat-4-soft text-cat-4-ink",
    stripe: "border-t-cat-4",
  },
  5: {
    solid: "bg-cat-5",
    soft: "bg-cat-5-soft",
    line: "border-cat-5-line",
    ink: "text-cat-5-ink",
    pill: "border-cat-5-line bg-cat-5-soft text-cat-5-ink",
    stripe: "border-t-cat-5",
  },
  6: {
    solid: "bg-cat-6",
    soft: "bg-cat-6-soft",
    line: "border-cat-6-line",
    ink: "text-cat-6-ink",
    pill: "border-cat-6-line bg-cat-6-soft text-cat-6-ink",
    stripe: "border-t-cat-6",
  },
  7: {
    solid: "bg-cat-7",
    soft: "bg-cat-7-soft",
    line: "border-cat-7-line",
    ink: "text-cat-7-ink",
    pill: "border-cat-7-line bg-cat-7-soft text-cat-7-ink",
    stripe: "border-t-cat-7",
  },
  8: {
    solid: "bg-cat-8",
    soft: "bg-cat-8-soft",
    line: "border-cat-8-line",
    ink: "text-cat-8-ink",
    pill: "border-cat-8-line bg-cat-8-soft text-cat-8-ink",
    stripe: "border-t-cat-8",
  },
};

/** FNV-1a, 32 bits: small, stable across runs and browsers, and well spread on short names. */
function fnv1a(text: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash;
}

/** The hue of a workflow: the same name always gives the same hue. */
export function workflowHue(name: string): Hue {
  return ((fnv1a(name.trim().toLowerCase()) % HUES.length) + 1) as Hue;
}

/** The hue of any name the console shows as an identity — a project as much as a workflow (S55-10). */
export const hueOf = workflowHue;

/** The classes of a workflow's hue. */
export function workflowHueClasses(name: string): HueClasses {
  return HUE_CLASSES[workflowHue(name)];
}
