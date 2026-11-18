// SPDX-License-Identifier: Apache-2.0
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { columnsFromGraph } from "@/components/workflows/board";
import {
  applyEvent,
  applyItems,
  cut,
  initialState,
  reduce,
  view,
  type LiveEntry,
  type LiveStep,
  type LiveView,
} from "@/lib/live-flow";
import type { WorkflowGraph, WorkItemDto } from "@/lib/types";

/**
 * S67-26. The reduction is written twice (here and in `packages/cli/src/tranibor_cli/live_flow.py`) and both
 * suites run EVERY file of `packages/contracts/fixtures/live/`: they list the directory, they do not name
 * the fixtures, so a fixture added is run by both (the Python suite checks that this file still does).
 */
const DIR = resolve(
  import.meta.dirname,
  "../../../packages/contracts/fixtures/live",
);

interface Fixture {
  name: string;
  graph: WorkflowGraph | null;
  items: WorkItemDto[];
  steps: LiveStep[];
  now: string;
  options: { ticker_size?: number; day_start?: string | null };
  expected: LiveView;
}

const files = readdirSync(DIR)
  .filter((name) => name.endsWith(".json"))
  .sort();
const fixtures: Fixture[] = files.map(
  (name) => JSON.parse(readFileSync(resolve(DIR, name), "utf8")) as Fixture,
);

describe("the live flow's reduction, on the shared fixtures", () => {
  it("finds the fixtures", () => {
    expect(files.length).toBeGreaterThanOrEqual(7);
    for (const [index, fixture] of fixtures.entries())
      expect(fixture.name + ".json").toBe(files[index]);
  });

  it.each(fixtures.map((f) => [f.name, f] as const))(
    "%s gives the expected lanes, cards and ticker",
    (_name, f) => {
      expect(
        reduce(f.graph ?? undefined, f.items, f.steps, f.now, f.options),
      ).toEqual(f.expected);
    },
  );

  it.each(fixtures.map((f) => [f.name, f] as const))(
    "%s: its lanes are the board's columns",
    (_name, f) => {
      const columns = columnsFromGraph(f.graph ?? undefined, f.items);
      // Only the first read: a step may move a card to a state the first read did not know.
      const lanes = reduce(
        f.graph ?? undefined,
        f.items,
        [],
        f.now,
        f.options,
      ).lanes;
      expect(
        lanes.map((l) => [
          l.state,
          l.display,
          l.kind ?? undefined,
          l.terminal,
          l.hors_workflow,
        ]),
      ).toEqual(
        columns.map((c) => [
          c.state,
          c.display,
          c.kind,
          Boolean(c.terminal),
          Boolean(c.horsWorkflow),
        ]),
      );
    },
  );

  it("is read by the Python suite too (it enumerates the same directory)", () => {
    const python = readFileSync(
      resolve(
        import.meta.dirname,
        "../../../packages/cli/tests/test_live_flow.py",
      ),
      "utf8",
    );
    expect(python).toContain('"fixtures" / "live"');
    expect(python).toContain('glob("*.json")');
  });
});

describe("the reduction's own rules", () => {
  const graph: WorkflowGraph = {
    nodes: [
      { id: "a", display: "A", kind: "agent" },
      { id: "b", display: "B", kind: "agent" },
    ],
    edges: [],
  };
  const item = (extra: Partial<WorkItemDto> = {}): WorkItemDto =>
    ({
      id: "w1",
      tracker_key: "K-1",
      title: "T",
      state: "a",
      project_slug: "p",
      totals: {},
      ...extra,
    }) as WorkItemDto;
  const entry = (extra: Partial<LiveEntry>): LiveEntry =>
    ({
      kind: "run",
      id: "r1",
      status: "running",
      work_item_id: "w1",
      stage_role: "dev",
      started_at: "2026-11-18T10:00:00Z",
      can_stop: true,
      ...extra,
    }) as LiveEntry;

  it("elapsed time follows the clock it is given, never below zero", () => {
    const state = applyEvent(
      initialState(graph, [item()]),
      "activity.upsert",
      entry({}),
    );
    expect(
      view(state, "2026-11-18T10:01:05Z").cards[0]?.running?.elapsed_ms,
    ).toBe(65_000);
    expect(
      view(state, "2026-11-18T09:00:00Z").cards[0]?.running?.elapsed_ms,
    ).toBe(0);
  });

  it("does not mutate the state it is given", () => {
    const first = initialState(graph, [item()]);
    const frozen = JSON.stringify([first.slots, first.ticker]);
    applyEvent(first, "activity.upsert", entry({}));
    applyItems(first, [item({ state: "b" })], "2026-11-18T10:00:00Z");
    expect(JSON.stringify([first.slots, first.ticker])).toBe(frozen);
  });

  it("cuts a text on one line", () => {
    expect(cut("a\n  b", 10)).toBe("a b");
    expect(cut("abcdef", 4)).toBe("abc…");
  });
});
