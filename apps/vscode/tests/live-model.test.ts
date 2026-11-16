// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { elapsed, figures, money, tokens } from "../src/live/format";
import { LiveModel, PERSONAL, type GroupNode } from "../src/live/model";
import { LOOKS, lookOf, stateOf, type State } from "../src/live/states";
import { run } from "./fakes/activity";

const NOW = new Date("2026-11-12T10:04:12Z");

describe("a state", () => {
  it("is an icon, a word and a theme colour, each its own", () => {
    for (const [state, look] of Object.entries(LOOKS)) {
      expect(look.word, state).toMatch(/\w/);
      expect(look.icon, state).toMatch(/\w/);
      expect(look.color, state).toMatch(/\w/);
    }
    const icons = Object.values(LOOKS).map((l) => l.icon);
    expect(new Set(icons).size).toBe(icons.length);
  });

  it("keeps the colours the product owner approved (ADR 0155 §3)", () => {
    const colours = Object.fromEntries(Object.entries(LOOKS).map(([s, l]) => [s, l.color])) as Record<
      State,
      string
    >;
    expect(colours).toMatchObject({
      done: "charts.green",
      running: "charts.blue",
      waiting: "charts.yellow",
      queued: "disabledForeground",
      failed: "charts.red",
    });
  });

  it.each([
    ["queued", "queued"],
    ["running", "running"],
    ["open", "running"],
    ["succeeded", "done"],
    ["completed", "done"],
    ["closed", "done"],
    ["failed", "failed"],
    ["timed_out", "failed"],
    ["cancelled", "stopped"],
  ])("reads %s as %s", (status, state) => {
    expect(stateOf({ status })).toBe(state);
  });

  it("shows a status it does not know as its own word, never as another state", () => {
    expect(stateOf({ status: "paused" })).toBeUndefined();
    expect(lookOf({ status: "paused" }).word).toBe("paused");
  });
});

describe("the figures", () => {
  it("are elapsed · tokens · cost", () => {
    expect(figures(run("r1"), NOW)).toBe("4m 12s · 15k tok · $0.42");
  });

  it("read — when the platform cannot say the spend, never 0", () => {
    expect(figures(run("r1", { spend: null }), NOW)).toBe("4m 12s · — · —");
    const withoutSpend = run("r1");
    delete withoutSpend.spend;
    expect(figures(withoutSpend, NOW)).toBe("4m 12s · — · —");
  });

  it("stop the clock at the end", () => {
    expect(elapsed("2026-11-12T10:00:00Z", "2026-11-12T11:02:00Z", NOW)).toBe("1h 02m");
    expect(elapsed("2026-11-12T10:04:10Z", null, NOW)).toBe("2s");
    expect(elapsed("not a date", null, NOW)).toBe("—");
  });

  it.each([
    [999, "999 tok"],
    [1_500, "1.5k tok"],
    [15_400, "15k tok"],
    [2_500_000, "2.5M tok"],
  ])("count %i tokens as %s", (count, text) => {
    expect(tokens(count)).toBe(text);
  });

  it("say a zero that the platform said", () => {
    expect(tokens(0)).toBe("0 tok");
    expect(money(0)).toBe("$0.00");
    expect(money(123.4)).toBe("$123");
  });
});

describe("the model", () => {
  function groups(model: LiveModel): GroupNode[] {
    return model.tree(NOW);
  }

  it("groups by project, then work item, newest first", () => {
    const model = new LiveModel();
    model.load({
      in_flight: [
        run("r1", { started_at: "2026-11-12T10:00:00Z" }),
        run("r2", { started_at: "2026-11-12T10:02:00Z", stage_role: "review" }),
        run("r3", { work_item_id: "wi-2", work_item_key: "SHOP-43", started_at: "2026-11-12T10:03:00Z" }),
      ],
      finished: [],
    });
    const [shop] = groups(model);
    expect(shop?.label).toBe("shop");
    const items = shop?.children as GroupNode[];
    expect(items.map((i) => i.label)).toEqual(["SHOP-43", "SHOP-42"]);
    expect(items[1]?.detail).toBe("feature");
    expect(items[1]?.children.map((c) => (c.kind === "entry" ? c.entry.id : ""))).toEqual(["r2", "r1"]);
  });

  it("puts what names no project in the person's space, and what names no work item under its project", () => {
    const model = new LiveModel();
    model.load({
      in_flight: [
        run("t1", { kind: "turn", project_slug: null, work_item_id: null, assistant: "helper" }),
        run("s1", { kind: "session", status: "open", work_item_id: null }),
      ],
      finished: [],
    });
    const labels = groups(model).map((g) => g.label);
    expect(labels).toEqual([PERSONAL, "shop"]);
    expect(groups(model)[1]?.children[0]?.kind).toBe("entry");
  });

  it("orders projects with something in flight before the others", () => {
    const model = new LiveModel();
    model.load({
      in_flight: [run("r1", { project_slug: "zoo" })],
      finished: [run("r0", { project_slug: "alpha", status: "succeeded", ended_at: "2026-11-12T10:01:00Z" })],
    });
    expect(groups(model).map((g) => [g.label, g.state])).toEqual([
      ["zoo", "running"],
      ["alpha", "done"],
    ]);
  });

  it("follows the stream: an upsert adds, an ended entry turns done, the count follows", () => {
    const model = new LiveModel();
    model.load({ in_flight: [], finished: [] });
    expect(model.apply("activity.upsert", JSON.stringify(run("r1")))).toBe(true);
    expect(model.running()).toBe(1);
    const ended = run("r1", { status: "succeeded", ended_at: "2026-11-12T10:04:00Z", can_stop: false });
    expect(model.apply("activity.ended", JSON.stringify(ended))).toBe(true);
    expect(model.running()).toBe(0);
    expect(model.get("run:r1")?.status).toBe("succeeded");
  });

  it("ignores what is not an entry", () => {
    const model = new LiveModel();
    expect(model.apply("ping", "{}")).toBe(false);
    expect(model.apply("activity.upsert", "{not json")).toBe(false);
    expect(model.apply("activity.upsert", JSON.stringify({ status: "running" }))).toBe(false);
  });

  it("drops what ended more than an hour ago, as the platform's list does", () => {
    const model = new LiveModel();
    model.load({
      in_flight: [],
      finished: [run("old", { status: "failed", ended_at: "2026-11-12T08:59:00Z" })],
    });
    expect(groups(model)).toEqual([]);
  });

  it("a list replaces what the stream had: nothing missed is guessed", () => {
    const model = new LiveModel();
    model.apply("activity.upsert", JSON.stringify(run("gone")));
    model.load({ in_flight: [run("r2")], finished: [] });
    expect(model.get("run:gone")).toBeUndefined();
    expect(model.running()).toBe(1);
  });
});
