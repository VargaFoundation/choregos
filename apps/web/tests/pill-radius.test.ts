// @vitest-environment node
/**
 * S39-03d: pills follow the theme (`radius.pill`), dots stay round.
 *
 * `rounded-full` is a circle whatever the theme says. A badge, chip, counter or bar written with it
 * keeps rounded corners on a square brand. Pill-shaped elements use `rounded-pill`
 * (`--radius-pill`, round by default, set by `radius.pill`); `rounded-full` is for dots, avatars,
 * spinners and circular icon buttons. This guard counts `rounded-full` per file against the list
 * below, each entry with the circle it is for: a new `rounded-full` on a pill fails here, and so
 * does an entry that is no longer true (the list says what is round on purpose today).
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..", "src");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "mocks" ? [] : sources(path);
    return /\.(tsx?|css)$/.test(name) ? [path] : [];
  });
}

/** `rounded-full` occurrences allowed per file, and what they are. */
const ROUND_ON_PURPOSE: Record<string, { count: number; what: string }> = {
  "app/globals.css": { count: 1, what: "this comment, which names the class" },
  "app/p/[slug]/board/page.tsx": { count: 1, what: "the status dot of a pill" },
  "app/p/[slug]/flow/page.tsx": { count: 1, what: "the status dot of a pill" },
  "app/p/[slug]/workflows/library/page.tsx": { count: 1, what: "a workflow's hue dot" },
  "app/p/[slug]/workflows/page.tsx": { count: 1, what: "a workflow's hue dot" },
  "components/accounts/avatar.tsx": { count: 1, what: "the avatar" },
  "components/accounts/linked-account-list.tsx": { count: 1, what: "the account's initial, an avatar" },
  "components/agents/tags.tsx": { count: 1, what: "the round remove button inside a chip" },
  "components/journey/journey-map.tsx": { count: 6, what: "station dots, rings and the numbered circles" },
  "components/live/live-kit.tsx": { count: 2, what: "the live dot and the ticker dots" },
  "components/ui.tsx": { count: 3, what: "the status dot, the avatar and the numbered circle" },
  "components/workflow-chip.tsx": { count: 1, what: "the workflow's hue dot inside the chip" },
  "components/workflows/board-section.tsx": { count: 1, what: "the section's hue dot" },
  "components/workflows/flow/flow-screen.tsx": { count: 1, what: "the round wall-mode exit button" },
  "components/workflows/flow/flow-view.tsx": { count: 1, what: "the status dot inside a flow node" },
};

describe("pills follow the theme, dots stay round (S39-03d)", () => {
  const found: Record<string, number> = {};
  for (const file of sources(SRC)) {
    const count = (readFileSync(file, "utf8").match(/rounded-full/g) ?? []).length;
    if (count > 0) found[relative(SRC, file)] = count;
  }

  it("keeps `rounded-full` to the circles listed, and lists no circle that is gone", () => {
    const expected = Object.fromEntries(Object.entries(ROUND_ON_PURPOSE).map(([f, e]) => [f, e.count]));
    expect(found).toEqual(expected);
  });

  it.each([
    ["components/ui.tsx", /rounded-pill border px-2 py-0\.5 leading-4/], // Pastille: Default, task, ...
    ["components/workflow-chip.tsx", /inline-flex max-w-full items-center gap-1\.5 rounded-pill/],
    ["components/edition.tsx", /rounded-pill border border-line/], // the Enterprise edition badge
    ["app/top-nav.tsx", /rounded-pill border border-waiting-line/], // RECONNECTING
    ["components/live/live-kit.tsx", /inline-flex items-center gap-2 rounded-pill/],
  ])("%s draws its pill with rounded-pill", (file, pattern) => {
    expect(readFileSync(join(SRC, file), "utf8")).toMatch(pattern);
  });

  it("declares the utility's variable, round by default", () => {
    const css = readFileSync(join(SRC, "app/globals.css"), "utf8");
    expect(css).toMatch(/--radius-pill:\s*9999px;/);
  });
});
