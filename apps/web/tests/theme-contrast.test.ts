// @vitest-environment node
/**
 * The console's contrast budget (ADR 0043), mode by mode.
 *
 * The design system gave text 19.8:1 in light and 19:1 in dark, and rules 1.26:1: all the contrast
 * was in the text, none in the structure. The console aims at the opposite: text that reads without
 * dazzling (7 to 17:1), control borders at 3:1 at least (WCAG 1.4.11), rules that can be seen. Axe
 * does not measure the contrast of borders or focus: this test does.
 */
import { describe, expect, it } from "vitest";
import { contrast, luminance, token, type Mode } from "./tokens";

const MODES: Mode[] = ["dark", "light"];
const BACKGROUNDS = ["--choregos-surface", "--choregos-raised", "--choregos-sidebar", "--choregos-surface-muted"];
const STATUSES = ["running", "waiting", "succeeded", "failed", "retrying", "neutral", "agent"];
// The categorical hues that orient (ADR 0120): a workflow, a section, a group of settings.
const HUES = [1, 2, 3, 4, 5, 6, 7, 8].map((n) => `cat-${n}`);

function ratio(first: string, second: string, mode: Mode): number {
  return contrast(token(first, mode), token(second, mode));
}

describe.each(MODES)("the console's contrast, in %s", (mode) => {
  it("text reads without dazzling: 7:1 at least, 17:1 at most, on every background", () => {
    for (const fond of BACKGROUNDS) {
      const r = ratio("--choregos-ink", fond, mode);
      expect(r, `ink on ${fond}`).toBeGreaterThanOrEqual(7);
      expect(r, `ink on ${fond}: too harsh`).toBeLessThanOrEqual(17);
    }
  });

  it("secondary text and hints pass AA (4.5:1) on every background", () => {
    for (const textToken of ["--choregos-ink-muted", "--choregos-ink-placeholder", "--choregos-accent-strong"]) {
      for (const fond of BACKGROUNDS) {
        expect(ratio(textToken, fond, mode), `${textToken} on ${fond}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("text on a solid action passes AA, at rest and on hover", () => {
    for (const [textToken, fond] of [
      ["--choregos-inverse-ink", "--choregos-inverse"],
      ["--choregos-inverse-ink", "--choregos-inverse-hover"],
      ["--choregos-accent-ink", "--choregos-accent"],
      ["--choregos-accent-ink", "--choregos-accent-hover"],
    ] as const) {
      expect(ratio(textToken, fond, mode), `${textToken} on ${fond}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("each status reads on the page, a card, a hover and its own badge", () => {
    for (const textToken of ["--choregos-ok", "--choregos-warn", "--choregos-danger"]) {
      for (const fond of BACKGROUNDS) {
        expect(ratio(textToken, fond, mode), `${textToken} on ${fond}`).toBeGreaterThanOrEqual(4.5);
      }
    }
    for (const fond of [...BACKGROUNDS, "--choregos-info-soft"]) {
      expect(ratio("--choregos-info", fond, mode), `--choregos-info on ${fond}`).toBeGreaterThanOrEqual(4.5);
    }
    for (const status of STATUSES) {
      const ink = `--choregos-${status}-ink`;
      for (const fond of [...BACKGROUNDS, `--choregos-${status}-soft`]) {
        expect(ratio(ink, fond, mode), `${ink} on ${fond}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("each categorical hue reads on the page, a card, and its own tint (ADR 0120)", () => {
    for (const hue of HUES) {
      const ink = `--choregos-${hue}-ink`;
      for (const fond of [...BACKGROUNDS, `--choregos-${hue}-soft`]) {
        expect(ratio(ink, fond, mode), `${ink} on ${fond}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("a control border and the focus can be seen (3:1), a rule too (1.3:1)", () => {
    for (const fond of ["--choregos-surface", "--choregos-raised"]) {
      expect(ratio("--choregos-line-strong", fond, mode), `border on ${fond}`).toBeGreaterThanOrEqual(3);
      expect(ratio("--choregos-line", fond, mode), `rule on ${fond}`).toBeGreaterThanOrEqual(1.3);
    }
    for (const fond of BACKGROUNDS) {
      expect(ratio("--choregos-focus", fond, mode), `focus on ${fond}`).toBeGreaterThanOrEqual(3);
    }
  });

  it("the sidebar is darker than the page", () => {
    expect(luminance(token("--choregos-sidebar", mode))).toBeLessThan(luminance(token("--choregos-surface", mode)));
  });
});
