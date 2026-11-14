// SPDX-License-Identifier: Apache-2.0
import type { ActivityList, CostReport, Me } from "@choregos/contracts/api";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChoregosClient } from "../src/api/client";
import { LiveFeed, POLL_MS, RECONNECT_MS, today, type FeedDeps } from "../src/live/feed";
import { run } from "./fakes/activity";
import { FakeApi } from "./fakes/api";

const LIST = "/api/v1/orgs/acme/activity";
const STREAM = "/api/v1/orgs/acme/activity/stream";
const COSTS = "/api/v1/orgs/acme/costs";
const NOW = new Date(2026, 10, 12, 10, 4, 12);

let api: FakeApi;
let feed: LiveFeed;
let sleeps: number[];
let wakers: (() => void)[];
let signedIn: boolean;
let organisation: string;

function report(cost: number): CostReport {
  return { group_by: "day", rows: [], total: { cost_usd: cost } };
}

beforeEach(async () => {
  api = await FakeApi.start();
  sleeps = [];
  wakers = [];
  signedIn = true;
  organisation = "acme";
  const deps: FeedDeps = {
    client: () =>
      Promise.resolve(
        signedIn
          ? new ChoregosClient({
              instanceUrl: api.url,
              credential: () => Promise.resolve("chg_t"),
              userAgent: "t",
            })
          : undefined,
      ),
    organisation: () => organisation,
    // The feed waits on this clock only: a test sees each wait and never sleeps for real.
    sleep: (ms, signal) => {
      sleeps.push(ms);
      return new Promise((resolve) => {
        wakers.push(resolve);
        signal.addEventListener("abort", () => {
          resolve();
        });
      });
    },
    log: () => undefined,
  };
  feed = new LiveFeed(deps, () => NOW);
});

afterEach(async () => {
  await feed.stop();
  await api.stop();
});

describe("the live feed", () => {
  it("lists, then follows the stream: an upsert adds, an ended entry turns done", async () => {
    api.json("GET", LIST, { in_flight: [run("r1")], finished: [] } satisfies ActivityList);
    api.json("GET", COSTS, report(1.24));
    const live = api.stream(STREAM);
    await feed.restart();
    await vi.waitFor(() => {
      expect(feed.state.kind).toBe("live");
    });
    expect(feed.model.running()).toBe(1);
    expect(feed.spendToday).toBe(1.24);

    live.send("activity.upsert", run("r2", { stage_role: "review" }), "e1");
    await vi.waitFor(() => {
      expect(feed.model.running()).toBe(2);
    });
    api.json("GET", COSTS, report(1.5));
    live.send("activity.ended", run("r1", { status: "succeeded", ended_at: "2026-11-12T10:04:00Z" }), "e2");
    await vi.waitFor(() => {
      expect(feed.model.get("run:r1")?.status).toBe("succeeded");
    });
    expect(feed.model.running()).toBe(1);
    // An entry that ended moves the day's spend: it is read again.
    await vi.waitFor(() => {
      expect(feed.spendToday).toBe(1.5);
    });
  });

  it("reads today's spend for today only", async () => {
    api.json("GET", LIST, { in_flight: [], finished: [] });
    api.json("GET", COSTS, report(0));
    api.stream(STREAM);
    await feed.restart();
    await vi.waitFor(() => {
      expect(api.requestsTo("GET", COSTS)).toHaveLength(1);
    });
    const [request] = api.requestsTo("GET", COSTS);
    expect(today(NOW)).toBe("2026-11-12");
    expect([request?.query.get("since"), request?.query.get("until")]).toEqual(["2026-11-12", "2026-11-12"]);
    await vi.waitFor(() => {
      expect(feed.spendToday).toBe(0);
    });
  });

  it("says nothing of the spend a person may not read", async () => {
    api.json("GET", LIST, { in_flight: [], finished: [] });
    api.problem("GET", COSTS, 403, "Forbidden", "reading costs needs an organisation role");
    api.stream(STREAM);
    await feed.restart();
    await vi.waitFor(() => {
      expect(feed.state.kind).toBe("live");
    });
    expect(feed.spendToday).toBeUndefined();
  });

  it("lists again when the platform closes the stream, then follows again", async () => {
    api.json("GET", LIST, { in_flight: [run("r1")], finished: [] });
    api.json("GET", COSTS, report(0));
    const live = api.stream(STREAM);
    await feed.restart();
    await live.opened;
    api.json("GET", LIST, { in_flight: [run("r1"), run("missed")], finished: [] });
    live.close();
    await vi.waitFor(() => {
      expect(sleeps).toEqual([RECONNECT_MS]);
    });
    // The wait is over: the feed lists again before it reconnects, and sees what the stream missed.
    wakers.shift()?.();
    await vi.waitFor(() => {
      expect(live.connections).toBe(2);
    });
    expect(feed.model.running()).toBe(2);
    expect(api.requestsTo("GET", LIST)).toHaveLength(2);
    expect(feed.state.kind).toBe("live");
  });

  it("falls back to listing every 30 s and says it is not live when the stream cannot be held", async () => {
    api.json("GET", LIST, { in_flight: [run("r1")], finished: [] });
    api.json("GET", COSTS, report(0));
    api.problem("GET", STREAM, 502, "Bad Gateway", "the proxy cut the stream");
    await feed.restart();
    await vi.waitFor(() => {
      expect(feed.state).toMatchObject({ kind: "polling", reason: "the proxy cut the stream" });
    });
    expect(sleeps).toEqual([POLL_MS]);
    expect(feed.model.running()).toBe(1);
  });

  it("stops at a refused credential rather than retrying it", async () => {
    api.problem("GET", LIST, 401, "Unauthorized", "expired API token");
    const states: string[] = [];
    feed.onDidChange(() => states.push(feed.state.kind));
    await feed.restart();
    await vi.waitFor(() => {
      expect(states.at(-1)).toBe("signed-out");
    });
    expect(states).toContain("connecting");
    expect(sleeps).toEqual([]);
    expect(api.requestsTo("GET", LIST)).toHaveLength(1);
  });

  it("shows nothing and asks nobody when the person is not signed in", async () => {
    signedIn = false;
    await feed.restart();
    expect(feed.state.kind).toBe("signed-out");
    expect(api.requests).toHaveLength(0);
  });

  it("follows the person's first organisation when the setting names none", async () => {
    organisation = "";
    const me: Me = {
      id: "u",
      email: "a@example.com",
      display_name: "A",
      memberships: [{ org: "acme", role: "developer" }],
    };
    api.json("GET", "/api/v1/me", me);
    api.json("GET", LIST, { in_flight: [], finished: [] });
    api.json("GET", COSTS, report(0));
    api.stream(STREAM);
    await feed.restart();
    await vi.waitFor(() => {
      expect(feed.state).toEqual({ kind: "live", org: "acme" });
    });
  });

  it("says so when the person belongs to no organisation", async () => {
    organisation = "";
    api.json("GET", "/api/v1/me", { id: "u", email: "a@example.com", display_name: "A", memberships: [] });
    await feed.restart();
    await vi.waitFor(() => {
      expect(feed.state).toEqual({
        kind: "no-organisation",
        reason: "you belong to no organisation on this instance",
      });
    });
  });

  it("says so when the organisation refuses the person", async () => {
    api.problem(
      "GET",
      LIST,
      403,
      "Forbidden",
      "reading what is in flight in acme requires being a member of it",
    );
    await feed.restart();
    await vi.waitFor(() => {
      expect(feed.state).toMatchObject({
        kind: "polling",
        reason: expect.stringContaining("member") as string,
      });
    });
  });
});
