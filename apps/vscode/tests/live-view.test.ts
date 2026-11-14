// SPDX-License-Identifier: Apache-2.0
import type { Me } from "@choregos/contracts/api";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type * as vscode from "vscode";
import { activate, COMMANDS, type Extension } from "../src/extension";
import { LiveFeed, type FeedState } from "../src/live/feed";
import type { LiveNode } from "../src/live/model";
import { LOOKS } from "../src/live/states";
import { LiveTree, statusText } from "../src/live/view";
import { run } from "./fakes/activity";
import { FakeApi } from "./fakes/api";
import { extensionContext, fake, ThemeColor, ThemeIcon } from "./fakes/vscode";

const NOW = new Date("2026-11-12T10:04:12Z");

function feedWith(state: FeedState, entries = [run("r1")], spend?: number): LiveFeed {
  const feed = new LiveFeed({
    client: () => Promise.resolve(undefined),
    organisation: () => "acme",
    sleep: () => Promise.resolve(),
    log: () => undefined,
  });
  feed.model.load({ in_flight: entries, finished: [] });
  feed.state = state;
  feed.spendToday = spend;
  return feed;
}

function items(tree: LiveTree, node?: LiveNode): { node: LiveNode; item: vscode.TreeItem }[] {
  return tree.getChildren(node).map((child) => ({ node: child, item: tree.getTreeItem(child) }));
}

describe("the Live tree", () => {
  it("draws project → work item → entry, each state an icon in a theme colour with its word", () => {
    const tree = new LiveTree(feedWith({ kind: "live", org: "acme" }), () => NOW);
    const [project] = items(tree);
    expect(project?.item.label).toBe("shop");
    const [workItem] = items(tree, project?.node);
    expect(workItem?.item).toMatchObject({ label: "SHOP-42", description: "feature" });
    expect(workItem?.item.accessibilityInformation?.label).toBe(
      "work item, SHOP-42, workflow feature, running",
    );
    const [entry] = items(tree, workItem?.node);
    expect(entry?.item.label).toBe("implement · coder");
    expect(entry?.item.description).toBe("4m 12s · 15k tok · $0.42");
    const icon = entry?.item.iconPath as ThemeIcon;
    expect(icon).toBeInstanceOf(ThemeIcon);
    expect(icon.id).toBe("sync~spin");
    expect(icon.color).toEqual(new ThemeColor("charts.blue"));
    expect(entry?.item.accessibilityInformation?.label).toBe(
      "implement · coder, running, 4m 12s, 15k tok, $0.42",
    );
    expect(entry?.item.contextValue).toBe("run.stoppable");
    expect(tree.getChildren(entry?.node)).toEqual([]);
  });

  it.each(Object.entries({ queued: "queued", succeeded: "done", failed: "failed", cancelled: "stopped" }))(
    "a %s run reads %s, in words and in its icon",
    (status, state) => {
      const look = LOOKS[state as keyof typeof LOOKS];
      const tree = new LiveTree(
        feedWith({ kind: "live", org: "acme" }, [run("r", { status, can_stop: false })]),
        () => NOW,
      );
      const [project] = items(tree);
      const [workItem] = items(tree, project?.node);
      const [entry] = items(tree, workItem?.node);
      expect((entry?.item.iconPath as ThemeIcon).id).toBe(look.icon);
      expect(entry?.item.accessibilityInformation?.label).toContain(`, ${look.word},`);
      expect(entry?.item.contextValue).toBe("run");
    },
  );

  it("shows unknown spend as —", () => {
    const tree = new LiveTree(
      feedWith({ kind: "live", org: "acme" }, [run("r", { spend: null })]),
      () => NOW,
    );
    const [project] = items(tree);
    const [workItem] = items(tree, project?.node);
    const [entry] = items(tree, workItem?.node);
    expect(entry?.item.description).toBe("4m 12s · — · —");
  });

  it("says when it is not live, and why", () => {
    expect(new LiveTree(feedWith({ kind: "live", org: "acme" })).message()).toBeUndefined();
    expect(
      new LiveTree(feedWith({ kind: "polling", org: "acme", reason: "the proxy cut the stream" })).message(),
    ).toBe("Not live: the proxy cut the stream. Listing again every 30 s.");
    expect(new LiveTree(feedWith({ kind: "connecting", org: "acme" })).message()).toBe("Connecting to acme…");
    expect(new LiveTree(feedWith({ kind: "no-organisation", reason: "no org" })).message()).toBe("no org");
  });
});

describe("the status line", () => {
  it("reads N running · — waiting for you · $X today", () => {
    const feed = feedWith({ kind: "live", org: "acme" }, [run("r1"), run("r2", { status: "queued" })], 1.24);
    expect(statusText(feed).text).toBe("$(pulse) 2 running · — waiting for you · $1.24 today");
  });

  it("never counts what it cannot know: waiting for you and an unreadable spend are —", () => {
    const feed = feedWith({ kind: "polling", org: "acme", reason: "lost" }, [], undefined);
    const { text, tooltip } = statusText(feed);
    expect(text).toBe("$(debug-disconnect) 0 running · — waiting for you · — today");
    expect(tooltip).toContain("Not live (lost)");
    expect(tooltip).toContain("Spend today: not readable");
  });

  it("asks to sign in when nobody is", () => {
    expect(statusText(feedWith({ kind: "signed-out" })).text).toBe("$(account) Choregos: sign in");
  });
});

describe("the extension, end to end on the fake API", () => {
  let api: FakeApi;
  let context: ReturnType<typeof extensionContext>;
  let extension: Extension;

  beforeEach(async () => {
    fake.reset();
    api = await FakeApi.start();
    fake.settings.set("choregos.instanceUrl", api.url);
    fake.settings.set("choregos.organisation", "acme");
    context = extensionContext("1.2.3");
    extension = activate(context as unknown as vscode.ExtensionContext);
  });

  afterEach(async () => {
    for (const item of context.subscriptions) item.dispose();
    await extension.feed.stop();
    await api.stop();
  });

  it("signs in, then shows what runs in the Live view and the status line", async () => {
    const status = fake.statusItems.get("choregos.status");
    await vi.waitFor(() => {
      expect(status?.text).toBe("$(account) Choregos: sign in");
    });
    expect(status?.command).toBe(COMMANDS.signIn);

    const me: Me = { id: "u", email: "ada@example.com", display_name: "Ada", memberships: [] };
    api.json("GET", "/api/v1/me", me);
    api.json("GET", "/api/v1/orgs/acme/activity", { in_flight: [run("r1")], finished: [] });
    api.json("GET", "/api/v1/orgs/acme/costs", { group_by: "day", rows: [], total: { cost_usd: 0.42 } });
    const live = api.stream("/api/v1/orgs/acme/activity/stream");
    fake.inputs.push("chg_ada");
    await fake.commands.get(COMMANDS.signIn)?.();

    await vi.waitFor(() => {
      expect(status?.text).toBe("$(pulse) 1 running · — waiting for you · $0.42 today");
    });
    expect(status?.command).toBe("workbench.view.extension.choregos");
    live.send("activity.upsert", run("r2"));
    await vi.waitFor(() => {
      expect(status?.text).toBe("$(pulse) 2 running · — waiting for you · $0.42 today");
    });
    const [request] = api.requestsTo("GET", "/api/v1/orgs/acme/activity/stream");
    expect(request?.headers.authorization).toBe("Bearer chg_ada");
    expect(fake.treeViews.get("choregos.live")?.message).toBe("");

    await fake.commands.get(COMMANDS.signOut)?.();
    await vi.waitFor(() => {
      expect(status?.text).toBe("$(account) Choregos: sign in");
    });
    expect(extension.live.getChildren()).toEqual([]);
  });
});
