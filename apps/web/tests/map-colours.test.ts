// @vitest-environment node
/**
 * The map colours (S24-04, ADR 0043). What draws (a box's bar, a shape's outline, a taken
 * send-back) must show (3:1, WCAG 1.4.11), what writes must read (4.5:1), on the page as on a
 * card, in both themes. Axe measures neither an SVG stroke nor a border.
 */
import { describe, expect, it } from "vitest";
import { SENT_BACK_COLOUR, STATUS_COLOUR } from "@/components/journey/colours";
import { KIND_COLOURS } from "@/components/workflows/map/layout";
import { contrast, token, type Mode } from "./tokens";

const FILLS = ["--choregos-surface", "--choregos-raised"];

function tokenNameOf(value: string): string {
  const tokenName = /^var\((--[a-z0-9-]+)\)$/.exec(value)?.[1];
  if (!tokenName) throw new Error(`not a token: ${value}`);
  return tokenName;
}

describe.each(["dark", "light"] as Mode[])("the map colours, in %s", (mode) => {
  const ratio = (colour: string, fill: string) => contrast(token(tokenNameOf(colour), mode), token(fill, mode));

  it("every kind of actor: a border that shows (3:1), a text that reads (4.5:1)", () => {
    for (const [kind, { border, text }] of Object.entries(KIND_COLOURS)) {
      for (const fill of FILLS) {
        expect(ratio(border, fill), `${kind}, border on ${fill}`).toBeGreaterThanOrEqual(3);
        expect(ratio(text, fill), `${kind}, text on ${fill}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("every journey status, and a taken send-back, show (3:1)", () => {
    for (const [status, stroke] of Object.entries({ ...STATUS_COLOUR, sentBack: SENT_BACK_COLOUR })) {
      for (const fill of FILLS) {
        expect(ratio(stroke, fill), `${status} on ${fill}`).toBeGreaterThanOrEqual(3);
      }
    }
  });
});

it("every kind and every living status has its colour: \"in progress\" and \"done\" are no longer mixed up (S23-12)", () => {
  const kinds = Object.values(KIND_COLOURS).map((c) => c.border);
  expect(new Set(kinds.slice(0, 5)).size).toBe(5);
  const { running, waiting, done, sent_back, failed } = STATUS_COLOUR;
  expect(new Set([running, waiting, done, sent_back, failed]).size).toBe(5);
});

it("every journey status takes the tone of the state it is, and every kind its own (ADR 0043)", () => {
  // "waiting for a person" used to be the ink (a pure white ring in dark) and "in progress" the
  // accent turquoise, the one of links and focus.
  expect(STATUS_COLOUR).toEqual({
    upcoming: "var(--choregos-neutral-solid)",
    running: "var(--choregos-running-ink)",
    waiting: "var(--choregos-waiting-ink)",
    done: "var(--choregos-succeeded-ink)",
    sent_back: "var(--choregos-retrying-ink)",
    failed: "var(--choregos-failed-ink)",
  });
  expect(SENT_BACK_COLOUR).toBe("var(--choregos-retrying-ink)");
  expect(KIND_COLOURS.agent.border).toBe("var(--choregos-agent-ink)");
  expect(KIND_COLOURS.human.border).toBe("var(--choregos-running-ink)");
});
